"""Генератор расчётно-пояснительной записки по каменным конструкциям.

Учебный инструмент: результаты требуют проверки проектировщиком по полному
тексту действующих нормативов и исходным данным конкретного задания.
Стандартная библиотека Python: tkinter + sqlite3, без внешних зависимостей.
"""
from __future__ import annotations

import html
import json
import math
import sqlite3
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "courseworks.sqlite3"
NORMS = (
    "СП 15.13330.2020 с Изм. № 1 (введено 22.01.2024); "
    "СП 20.13330.2016 с Изм. № 1–6 (последнее — 25.09.2024); "
    "ГОСТ 27751-2014; СП 63.13330.2018; СП 427.1325800.2018; "
    "СП 70.13330.2012; СП 131.13330.2020."
)


def num(value: object, default: float = 0.0) -> float:
    """Принимает запятую как десятичный разделитель."""
    try:
        return float(str(value).strip().replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return default


def fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


@dataclass
class Load:
    title: str
    kind: str = "постоянная"
    normative: float = 0.0  # kN/m²
    gamma_f: float = 1.1

    @property
    def design(self) -> float:
        return self.normative * self.gamma_f


@dataclass
class InputData:
    title: str = "Курсовой проект"
    student: str = ""
    group: str = ""
    teacher: str = ""
    variant: str = ""
    city: str = ""
    purpose: str = "Административное здание"
    brick_mark: str = "М150"
    mortar_mark: str = "М75"
    masonry_r: float = 2.0  # MPa: must be supplied/checked by user
    alpha: float = 1000.0
    floors: int = 6  # includes basement
    floor_height: float = 3.6
    basement_height: float = 3.0
    grid_x: float = 5.6
    grid_y: float = 6.4
    responsibility: float = 1.0
    masonry_density: float = 18.0  # kN/m³
    soil_r: float = 0.24  # MPa
    snow_normative: float = 1.5
    snow_ce: float = 1.0
    snow_ct: float = 1.0
    snow_mu: float = 1.0
    snow_gamma: float = 1.4
    column_side: float = 51.0  # cm
    pier_width: float = 120.0  # cm
    pier_height: float = 51.0  # cm
    opening_width: float = 140.0  # cm
    support_length: float = 25.0  # cm
    reinforcement_diameter: float = 4.0  # mm
    reinforcement_percent: float = 0.4
    jacket_thickness: float = 6.0  # cm
    jacket_concrete_r: float = 4.3  # MPa (B7.5, confirm for design)
    loads_roof: list[Load] = field(default_factory=lambda: [
        Load("Плита покрытия", "постоянная", 4.2, 1.1),
        Load("Пароизоляция", "постоянная", 0.005, 1.3),
        Load("Утеплитель", "постоянная", 0.18, 1.2),
        Load("Стяжка", "постоянная", 0.40, 1.3),
        Load("Гидроизоляционный ковёр", "постоянная", 0.15, 1.3),
    ])
    loads_floor: list[Load] = field(default_factory=lambda: [
        Load("Плита перекрытия", "постоянная", 4.2, 1.1),
        Load("Цементно-песчаная стяжка", "постоянная", 0.44, 1.3),
        Load("Покрытие пола", "постоянная", 0.08, 1.1),
        Load("Полезная нагрузка", "временная", 2.0, 1.2),
    ])

    @classmethod
    def from_dict(cls, raw: dict) -> "InputData":
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        for name in ("loads_roof", "loads_floor"):
            known[name] = [Load(**x) for x in known.get(name, [])]
        return cls(**known)


class Store:
    def __init__(self, path: Path) -> None:
        self.con = sqlite3.connect(path)
        self.con.execute("""CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def save(self, data: InputData, project_id: int | None) -> int:
        now = date.today().isoformat()
        payload = json.dumps(asdict(data), ensure_ascii=False)
        if project_id:
            self.con.execute("UPDATE projects SET title=?, payload=?, updated_at=? WHERE id=?",
                             (data.title, payload, now, project_id))
        else:
            cur = self.con.execute(
                "INSERT INTO projects(title,payload,created_at,updated_at) VALUES(?,?,?,?)",
                (data.title, payload, now, now))
            project_id = cur.lastrowid
        self.con.commit()
        return int(project_id)

    def all(self) -> list[tuple[int, str, str]]:
        return list(self.con.execute(
            "SELECT id,title,updated_at FROM projects ORDER BY updated_at DESC,id DESC"))

    def get(self, project_id: int) -> InputData:
        row = self.con.execute("SELECT payload FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        return InputData.from_dict(json.loads(row[0]))


def floor_reduction(area: float) -> float:
    """Коэффициент снижения равномерной временной нагрузки.

    Формула оставлена явной для проверки по применимому пункту СП 20:
    ψ = 0,5 + 0,5√(A1/A), ограничена диапазоном [0,5; 1].
    """
    return min(1.0, max(0.5, 0.5 + 0.5 * math.sqrt(9.0 / max(area, 9.0))))


def buckling_phi(length_cm: float, side_cm: float, alpha: float) -> float:
    """Консервативная интерполяционная аппроксимация для учебной проверки.

    Табличное φ СП 15 остаётся определяющим; программа выводит предупреждение
    и позволяет принять расчёт только после сверки с таблицей 7.1.
    """
    slenderness = length_cm / max(side_cm, 1)
    limit = max(alpha / 100, 1)
    return min(1.0, max(0.15, 1.0 - (slenderness / limit) ** 2))


class Calculator:
    def __init__(self, d: InputData) -> None:
        self.d = d
        self.area = d.grid_x * d.grid_y
        self.roof_perm = sum(x.design for x in d.loads_roof if x.kind == "постоянная")
        self.roof_snow = d.snow_normative * d.snow_ce * d.snow_ct * d.snow_mu * d.snow_gamma
        self.floor_perm = sum(x.design for x in d.loads_floor if x.kind == "постоянная")
        self.floor_live = sum(x.design for x in d.loads_floor if x.kind != "постоянная")
        self.psi = floor_reduction(self.area)

    def central_load(self, floors_above: int | None = None) -> float:
        n = self.d.floors - 2 if floors_above is None else floors_above
        n = max(0, n)
        per_area = self.roof_perm + self.roof_snow + n * (self.floor_perm + self.psi * self.floor_live)
        return self.area * per_area * self.d.responsibility * 1.05

    def capacity(self, side_cm: float, length_m: float | None = None, r: float | None = None) -> tuple[float, float, float]:
        length_cm = (length_m if length_m is not None else self.d.basement_height * .9) * 100
        phi = buckling_phi(length_cm, side_cm, self.d.alpha)
        mg = 1.0 if side_cm >= 30 else 0.9
        gamma_c = 0.8 if (side_cm / 100) ** 2 < .3 else 1.0
        return mg * phi * gamma_c * (r or self.d.masonry_r) * 0.1 * side_cm ** 2, phi, gamma_c

    def warnings(self) -> list[str]:
        d = self.d
        w = []
        if not (0.1 <= d.reinforcement_percent <= 1.0):
            w.append("Процент сетчатого армирования вне учебного диапазона 0,1–1,0 %: требуется отдельная проверка по п. 7.31 СП 15.")
        if d.floors < 2 or d.grid_x <= 0 or d.grid_y <= 0:
            w.append("Количество этажей и сетка колонн должны быть положительными.")
        if d.masonry_r <= 0:
            w.append("Расчётное сопротивление кладки R не задано. Его нельзя автоматически получать только из марки кирпича и раствора.")
        if not d.city:
            w.append("Не указан населённый пункт: снеговой район и Sg должны быть подтверждены вручную.")
        if self.roof_snow <= 0:
            w.append("Снеговая нагрузка не задана.")
        if self.d.floors < 3:
            w.append("Для задач подвала, надстройки и проверки этажа задано недостаточно этажей; проверьте применимость выбранных разделов.")
        if d.support_length <= 0 or d.opening_width <= 0:
            w.append("Для расчётов местного сжатия и перемычки требуются ненулевые длина опирания и ширина проёма.")
        w.append("φ определён аппроксимацией для учебного подбора. Перед оформлением проверьте его по таблице 7.1 СП 15.13330.2020.")
        return w

    def report(self) -> str:
        d, n = self.d, self.central_load()
        cap, phi, gc = self.capacity(d.column_side)
        side_req = math.sqrt(n / max(d.masonry_r * .1, .001))
        mesh_r = min(2 * d.masonry_r, d.masonry_r * (1 + d.reinforcement_percent / 100 * 100))
        mesh_cap, mesh_phi, mesh_gc = self.capacity(d.column_side, r=mesh_r)
        ac = d.pier_height * d.pier_width
        # Simplified eccentricity: reaction under the beam + self-weight, transparent inputs.
        beam_reaction = (self.floor_perm + self.psi * self.floor_live) * self.area / 2
        e0 = max(0.0, d.support_length / 2 - d.pier_height / 6)
        ac_compressed = max(ac * (1 - 2 * e0 / max(d.pier_height, 1)), ac * .2)
        pier_cap = .1 * d.masonry_r * ac_compressed
        bearing_ac = d.support_length * 40
        bearing_a = min((2 * d.pier_height + 40) * d.support_length, 4 * bearing_ac)
        xi = min(2.0, math.sqrt(bearing_a / max(bearing_ac, 1)))
        bearing_cap = .1 * d.masonry_r * xi * bearing_ac
        nn = n / 1.15
        footing_side = math.sqrt(1.1 * nn / max(d.soil_r * 1000, 1))  # m
        lintel_l = d.opening_width / 100
        lintel_h = max(.25 * lintel_l, .308)
        lintel_q = (d.pier_height / 100) * lintel_h * d.masonry_density * 1.1
        lintel_m = lintel_q * lintel_l ** 2 / 8
        extended = self.central_load(max(0, d.floors - 1))
        old_side = d.column_side
        jacket_side = old_side + 2 * d.jacket_thickness
        masonry_part, _, _ = self.capacity(old_side)
        jacket_area = jacket_side ** 2 - old_side ** 2
        jacket_cap = .1 * .35 * d.jacket_concrete_r * jacket_area

        def verdict(demand: float, resistance: float) -> str:
            return "обеспечена" if resistance >= demand else "НЕ обеспечена"
        return f"""
        <h1>{html.escape(d.title)}</h1>
        <p><b>Студент:</b> {html.escape(d.student or "не указан")} &nbsp; <b>Группа:</b> {html.escape(d.group or "не указана")}<br>
        <b>Вариант:</b> {html.escape(d.variant or "индивидуальные исходные данные")} &nbsp; <b>Дата:</b> {date.today().strftime("%d.%m.%Y")}</p>
        <h2>1. Исходные данные</h2>
        <p>Здание: {html.escape(d.purpose)}; населённый пункт: {html.escape(d.city or "не указан")}. Кладка: кирпич {html.escape(d.brick_mark)} на растворе {html.escape(d.mortar_mark)}.
        Принято R = {fmt(d.masonry_r)} МПа, α = {fmt(d.alpha,0)}. Этажей (включая подвальный): {d.floors};
        высота этажа {fmt(d.floor_height)} м; сетка {fmt(d.grid_x)}×{fmt(d.grid_y)} м.</p>
        <p>Грузовая площадь центрального столба A<sub>гр</sub> = {fmt(self.area)} м². Нормативная база: {NORMS}</p>
        <h2>2. Сбор нагрузок</h2>
        {load_table("Покрытие", d.loads_roof, self.roof_snow, d)}
        {load_table("Перекрытие", d.loads_floor, None, d)}
        <p>Коэффициент снижения временной нагрузки ψ(A={fmt(self.area)} м²) = {fmt(self.psi,3)}. Значение должно быть сверено с применимым сочетанием по СП 20.</p>
        <h2>3. Центрально-сжатый элемент подвала</h2>
        <p>N = A<sub>гр</sub>·[q<sub>покр</sub> + n·q<sub>пер</sub>]·γ<sub>n</sub>·1,05 =
        {fmt(n)} кН; n = {max(d.floors-2,0)}. Предварительно требуемая сторона квадратного столба
        a<sub>треб</sub> = √(N/(0,1R)) = {fmt(side_req)} см (без учёта φ и γ<sub>c</sub>).</p>
        <p>Принято сечение {fmt(d.column_side)}×{fmt(d.column_side)} см; φ = {fmt(phi,3)}, m<sub>g</sub> = 1,00,
        γ<sub>c</sub> = {fmt(gc,2)}. N<sub>Rd</sub> = {fmt(cap)} кН. Прочность {verdict(n, cap)}.</p>
        <h2>4. Проверка центрально-сжатого элемента этажа</h2>
        <p>Для заданного сечения расчётная несущая способность равна {fmt(cap)} кН. Нагрузку для конкретного этажа задают числом перекрытий выше рассматриваемого сечения; это исключает ошибку образцов, где этаж и число учтённых перекрытий расходятся.</p>
        <h2>5. Внецентренно-сжатый простенок</h2>
        <p>Сечение {fmt(d.pier_height)}×{fmt(d.pier_width)} см; опорная реакция ригеля (учебная схема) P = {fmt(beam_reaction)} кН;
        эксцентриситет e<sub>0</sub> = {fmt(e0)} см; сжатая площадь A<sub>c</sub> = {fmt(ac_compressed)} см².
        Упрощённая несущая способность R·A<sub>c</sub> = {fmt(pier_cap)} кН. Для окончательного расчёта необходимы геометрия стены, l<sub>0</sub>, φ, φ<sub>c</sub>, ω и m<sub>g</sub> по разделу 7 СП 15.</p>
        <h2>6. Столб с поперечным сетчатым армированием (подбор)</h2>
        <p>Принято μ = {fmt(d.reinforcement_percent)} %, проволока B500 Ø{fmt(d.reinforcement_diameter)} мм.
        R<sub>sk</sub> ограничено 2R: {fmt(mesh_r)} МПа. При том же сечении N<sub>Rd</sub> = {fmt(mesh_cap)} кН,
        φ = {fmt(mesh_phi,3)}, γ<sub>c</sub> = {fmt(mesh_gc,2)}. Прочность {verdict(n, mesh_cap)}.</p>
        <h2>7. Столб с сетчатым армированием (проверка)</h2>
        <p>Расчёт выполнен для произвольного вводимого μ. Программа не назначает число стержней и шаг без размера ячейки, защитных слоёв и конструктивных ограничений; их следует задать на чертеже и проверить по СП 15.</p>
        <h2>8. Местное сжатие (смятие) кладки</h2>
        <p>A<sub>см</sub> = {fmt(bearing_ac)} см²; расчётная площадь A = {fmt(bearing_a)} см²; ξ = {fmt(xi,3)} (не более 2).
        Учебная оценка N<sub>c</sub> = ξ·R·A<sub>см</sub> = {fmt(bearing_cap)} кН. Сравнить с P = {fmt(beam_reaction)} кН:
        прочность {verdict(beam_reaction, bearing_cap)}. Для окончательного расчёта добавьте ψ и d по реальной эпюре давления.</p>
        <h2>9. Центрально загруженный фундамент</h2>
        <p>Нормативная нагрузка N<sub>n</sub>≈N/1,15 = {fmt(nn)} кН. С учётом 10 % массы фундамента и грунта
        требуемая сторона квадратной подошвы a<sub>ф</sub> = {fmt(footing_side)} м при R<sub>гр</sub> = {fmt(d.soil_r)} МПа.
        Геометрию ступеней и местное сжатие под столбом следует проверить отдельно.</p>
        <h2>10. Рядовая кирпичная перемычка</h2>
        <p>Пролёт l = {fmt(lintel_l)} м; конструктивная высота h<sub>k</sub> = {fmt(lintel_h)} м; q = {fmt(lintel_q)} кН/м;
        M = ql²/8 = {fmt(lintel_m)} кН·м. Результат — исходная проверка: необходим расчёт распорной схемы, опирания и армирования по СП 15.</p>
        <h2>11. Надстройка одного этажа</h2>
        <p>Расчётная нагрузка после надстройки: N = {fmt(extended)} кН. Сравнение с исходной несущей способностью {fmt(cap)} кН:
        прочность {verdict(extended, cap)}.</p>
        <h2>12. Усиление железобетонной обоймой</h2>
        <p>Толщина обоймы {fmt(d.jacket_thickness)} см; наружная сторона {fmt(jacket_side)} см.
        Учебная добавка к несущей способности бетона (m<sub>b</sub>=0,35) = {fmt(jacket_cap)} кН; суммарно
        N<sub>Rd</sub>≈{fmt(masonry_part+jacket_cap)} кН. Арматура, анкеровка, совместная работа и расчёт по СП 427/СП 63 обязательны.</p>
        <h2>Контроль и ограничения</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in self.warnings())}</ul>
        <p><b>Вывод:</b> документ автоматически формирует воспроизводимый учебный расчёт. Он не является проектной документацией и не заменяет проверку квалифицированным проектировщиком.</p>
        """


def load_table(title: str, loads: list[Load], snow: float | None, d: InputData) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(x.title)}</td><td>{html.escape(x.kind)}</td><td>{fmt(x.normative,3)}</td>"
        f"<td>{fmt(x.gamma_f,2)}</td><td>{fmt(x.design,3)}</td></tr>" for x in loads)
    if snow is not None:
        rows += f"<tr><td>Снеговая нагрузка</td><td>временная</td><td>{fmt(d.snow_normative,3)}</td><td>{fmt(d.snow_gamma,2)}</td><td>{fmt(snow,3)}</td></tr>"
    return f"<h3>{title}</h3><table><tr><th>Нагрузка</th><th>Вид</th><th>Нормативная, кН/м²</th><th>γf</th><th>Расчётная, кН/м²</th></tr>{rows}</table>"


class App(tk.Tk):
    fields = [
        ("title", "Название работы"), ("student", "Студент"), ("group", "Группа"), ("teacher", "Проверил"),
        ("variant", "Вариант"), ("city", "Населённый пункт"), ("purpose", "Назначение здания"),
        ("brick_mark", "Марка кирпича"), ("mortar_mark", "Марка раствора"), ("masonry_r", "R кладки, МПа"),
        ("alpha", "α кладки"), ("floors", "Этажей с подвалом"), ("floor_height", "Высота этажа, м"),
        ("basement_height", "Высота подвала, м"), ("grid_x", "Сетка X, м"), ("grid_y", "Сетка Y, м"),
        ("responsibility", "γn"), ("masonry_density", "Плотность кладки, кН/м³"), ("soil_r", "R грунта, МПа"),
        ("snow_normative", "Sg снег, кН/м²"), ("snow_ce", "ce"), ("snow_ct", "ct"), ("snow_mu", "μ снега"),
        ("snow_gamma", "γf снега"), ("column_side", "Сторона столба, см"), ("pier_height", "Простенок h, см"),
        ("pier_width", "Простенок b, см"), ("opening_width", "Проём, см"), ("support_length", "Опирание, см"),
        ("reinforcement_diameter", "Ø сетки, мм"), ("reinforcement_percent", "μ армирования, %"),
        ("jacket_thickness", "Обойма, см"), ("jacket_concrete_r", "R бетона обоймы, МПа"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.title("Генератор работ — каменные конструкции")
        self.geometry("1230x760")
        self.store, self.project_id, self.data = Store(DB_PATH), None, InputData()
        self.vars = {name: tk.StringVar() for name, _ in self.fields}
        self._build()
        self.fill()

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=8); toolbar.pack(fill="x")
        for text, command in [("Новый", self.new), ("Сохранить в БД", self.save), ("Открыть", self.open_project),
                              ("Импорт JSON", self.import_json), ("Экспорт JSON", self.export_json),
                              ("Сформировать HTML", self.export_html)]:
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=3)
        ttk.Label(toolbar, text="  Расчёт учебный; перед применением сверяйте нормативы.").pack(side="right")
        main = ttk.PanedWindow(self, orient="horizontal"); main.pack(expand=True, fill="both", padx=8, pady=(0,8))
        left = ttk.Notebook(main); main.add(left, weight=1)
        info = ttk.Frame(left, padding=8); left.add(info, text="Исходные данные")
        canvas = tk.Canvas(info, highlightthickness=0); scroll = ttk.Scrollbar(info, command=canvas.yview)
        form = ttk.Frame(canvas); form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=form, anchor="nw"); canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        for row, (name, label) in enumerate(self.fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.vars[name], width=38).grid(row=row, column=1, sticky="ew", padx=(10,0), pady=3)
        form.columnconfigure(1, weight=1)
        roof = ttk.Frame(left, padding=8); left.add(roof, text="Нагрузки покрытия")
        floor = ttk.Frame(left, padding=8); left.add(floor, text="Нагрузки перекрытия")
        self.roof_tree = self.make_load_editor(roof, "roof")
        self.floor_tree = self.make_load_editor(floor, "floor")
        right = ttk.Frame(main, padding=8); main.add(right, weight=1)
        ttk.Button(right, text="Рассчитать и обновить просмотр", command=self.preview).pack(anchor="w", pady=(0,6))
        self.output = tk.Text(right, wrap="word", font=("Segoe UI", 10)); self.output.pack(expand=True, fill="both")

    def make_load_editor(self, parent: ttk.Frame, which: str) -> ttk.Treeview:
        columns = ("title", "kind", "normative", "gamma")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=18)
        for key, label, width in zip(columns, ("Наименование", "Вид", "Нормативная", "γf"), (280,120,120,80)):
            tree.heading(key, text=label); tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True)
        box = ttk.Frame(parent); box.pack(fill="x", pady=6)
        for text, func in [("Добавить", lambda: self.add_load(which)), ("Удалить", lambda: self.del_load(tree))]:
            ttk.Button(box, text=text, command=func).pack(side="left", padx=3)
        ttk.Label(box, text="Двойной щелчок — редактировать строку.").pack(side="left", padx=10)
        tree.bind("<Double-1>", lambda e: self.edit_load(tree))
        return tree

    def data_from_form(self) -> InputData:
        raw = asdict(self.data)
        for name, _ in self.fields:
            old = getattr(self.data, name)
            raw[name] = int(num(self.vars[name].get(), old)) if isinstance(old, int) else (
                num(self.vars[name].get(), old) if isinstance(old, float) else self.vars[name].get().strip())
        raw["loads_roof"] = self.tree_loads(self.roof_tree)
        raw["loads_floor"] = self.tree_loads(self.floor_tree)
        return InputData.from_dict(raw)

    @staticmethod
    def tree_loads(tree: ttk.Treeview) -> list[Load]:
        return [Load(str(v[0]), str(v[1]), num(v[2]), num(v[3], 1.0)) for i in tree.get_children() if (v := tree.item(i)["values"])]

    def fill(self) -> None:
        for name, _ in self.fields: self.vars[name].set(str(getattr(self.data, name)))
        for tree, loads in ((self.roof_tree, self.data.loads_roof), (self.floor_tree, self.data.loads_floor)):
            tree.delete(*tree.get_children())
            for x in loads: tree.insert("", "end", values=(x.title, x.kind, x.normative, x.gamma_f))
        self.preview()

    def add_load(self, which: str) -> None:
        (self.roof_tree if which == "roof" else self.floor_tree).insert("", "end", values=("Новая нагрузка", "постоянная", "0", "1,1"))

    @staticmethod
    def del_load(tree: ttk.Treeview) -> None:
        for item in tree.selection(): tree.delete(item)

    def edit_load(self, tree: ttk.Treeview) -> None:
        selected = tree.selection()
        if not selected: return
        item, vals = selected[0], tree.item(selected[0])["values"]
        dialog = tk.Toplevel(self); dialog.title("Нагрузка"); dialog.transient(self); dialog.grab_set()
        names = ("Наименование", "Вид (постоянная/временная)", "Нормативная, кН/м²", "γf")
        variables = [tk.StringVar(value=str(v)) for v in vals]
        for r, (label, var) in enumerate(zip(names, variables)):
            ttk.Label(dialog, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=4)
            ttk.Entry(dialog, textvariable=var, width=35).grid(row=r, column=1, padx=8, pady=4)
        ttk.Button(dialog, text="OK", command=lambda: (tree.item(item, values=[x.get() for x in variables]), dialog.destroy())).grid(row=4, column=1, sticky="e", padx=8, pady=8)

    def preview(self) -> None:
        try:
            self.data = self.data_from_form()
            text = html.unescape(Calculator(self.data).report())
            text = text.replace("<h1>", "\n").replace("</h1>", "\n").replace("<h2>", "\n\n").replace("</h2>", "\n")
            text = text.replace("<h3>", "\n").replace("</h3>", "\n").replace("<p>", "").replace("</p>", "\n")
            text = text.replace("<li>", "• ").replace("</li>", "\n").replace("<ul>", "").replace("</ul>", "")
            import re
            text = re.sub(r"<[^>]+>", " | ", text)
            self.output.delete("1.0", "end"); self.output.insert("1.0", textwrap.dedent(text).strip())
        except Exception as exc:
            self.output.delete("1.0", "end"); self.output.insert("1.0", f"Ошибка входных данных: {exc}")

    def new(self) -> None:
        self.project_id, self.data = None, InputData(); self.fill()

    def save(self) -> None:
        self.data = self.data_from_form(); self.project_id = self.store.save(self.data, self.project_id)
        messagebox.showinfo("Сохранено", f"Проект сохранён в SQLite (ID {self.project_id}).")

    def open_project(self) -> None:
        rows = self.store.all()
        if not rows: return messagebox.showinfo("Открыть", "Сохранённых проектов пока нет.")
        dialog = tk.Toplevel(self); dialog.title("Открыть проект"); dialog.geometry("500x300")
        tree = ttk.Treeview(dialog, columns=("title","date"), show="headings")
        tree.heading("title", text="Название"); tree.heading("date", text="Изменён")
        tree.pack(expand=True, fill="both", padx=8, pady=8)
        for ident, title, updated in rows: tree.insert("", "end", iid=str(ident), values=(title, updated))
        def choose() -> None:
            if tree.selection():
                self.project_id = int(tree.selection()[0]); self.data = self.store.get(self.project_id); self.fill(); dialog.destroy()
        ttk.Button(dialog, text="Открыть", command=choose).pack(pady=(0,8))

    def export_json(self) -> None:
        self.data = self.data_from_form()
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path: Path(path).write_text(json.dumps(asdict(self.data), ensure_ascii=False, indent=2), encoding="utf-8")

    def import_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path: return
        try:
            self.data = InputData.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
            self.project_id = None; self.fill()
        except Exception as exc: messagebox.showerror("Импорт", f"Не удалось прочитать файл:\n{exc}")

    def export_html(self) -> None:
        self.data = self.data_from_form()
        path = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML для Word/браузера", "*.html")])
        if not path: return
        document = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><title>{html.escape(self.data.title)}</title>
        <style>body{{font-family:'Times New Roman',serif;max-width:850px;margin:25mm auto;line-height:1.45}}h1{{text-align:center}}h2{{margin-top:1.5em}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #333;padding:5px;text-align:left}}th{{background:#eee}}</style>
        <body>{Calculator(self.data).report()}</body></html>"""
        Path(path).write_text(document, encoding="utf-8")
        messagebox.showinfo("Экспорт", "HTML создан. Его можно открыть в браузере или Microsoft Word и сохранить как DOCX.")


if __name__ == "__main__":
    App().mainloop()
