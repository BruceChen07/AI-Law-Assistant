from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import math
import textwrap
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
SVG_PATH = ROOT / "ai-law-assistant-high-level-architecture.svg"
PNG_PATH = ROOT / "ai-law-assistant-high-level-architecture.png"

WIDTH = 2400
HEIGHT = 1760


COLORS = {
    "bg": "#f4f7fb",
    "ink": "#17324d",
    "muted": "#5f7387",
    "panel_fill": "#fbfcfe",
    "panel_border": "#d6e1ec",
    "title": "#0f2740",
    "sync": "#2b6cb0",
    "async": "#d97706",
    "config": "#6b7280",
    "shadow": "#dbe7f3",
    "user": "#e8f1fb",
    "frontend": "#e9f7ef",
    "backend": "#eef2ff",
    "domain": "#f5efff",
    "ai": "#fff5e8",
    "data": "#ecfdf3",
    "ops": "#fff1f2",
    "external": "#f3f4f6",
    "legend": "#ffffff",
    "white": "#ffffff",
}


@dataclass
class Node:
    key: str
    x: int
    y: int
    w: int
    h: int
    fill: str
    title: str
    lines: list[str]


@dataclass
class Panel:
    x: int
    y: int
    w: int
    h: int
    title: str
    subtitle: str


@dataclass
class Edge:
    start: str
    start_side: str
    end: str
    end_side: str
    style: str
    label: str = ""
    bend: tuple[int, int] | None = None


def pick_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/simhei.ttf" if bold else "C:/Windows/Fonts/simsun.ttc",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


TITLE_FONT = pick_font(42, bold=True)
SUBTITLE_FONT = pick_font(22)
PANEL_TITLE_FONT = pick_font(24, bold=True)
PANEL_SUBTITLE_FONT = pick_font(18)
NODE_TITLE_FONT = pick_font(25, bold=True)
NODE_TEXT_FONT = pick_font(19)
LEGEND_FONT = pick_font(18)


PANELS = [
    Panel(40, 120, 1660, 170, "1. Actors & Access", "业务使用者、管理员与本地浏览器入口"),
    Panel(40, 320, 1660, 170, "2. Experience Layer", "React/Vite 单页前端、API 适配与会话入口"),
    Panel(40, 520, 1660, 170, "3. Application Layer", "FastAPI 应用装配、路由、鉴权与管理接口"),
    Panel(40, 720, 1660, 300, "4. Domain Services", "合同审计、法规导入检索、税务审计、能力与运行态编排"),
    Panel(40, 1050, 1660, 360, "5. AI & Data Platform", "LLM/OCR/Embedding/Memory/RAG/持久化/导出与观测"),
    Panel(1740, 120, 620, 1240, "6. Local Runtime & External Dependencies", "本地模型、OCR 工具链、文件系统、可选组件与部署控制"),
    Panel(1740, 1410, 620, 300, "Legend & Core Flow", "连线语义与关键数据流编号"),
]


NODES = [
    Node("users", 90, 165, 350, 90, COLORS["user"], "Business Users / Analysts", ["合同上传、法规查询、税审分析、报告查看"]),
    Node("ops", 470, 165, 350, 90, COLORS["user"], "Admins / Operators", ["用户与模型配置、运行监控、部署与巡检"]),
    Node("browser", 850, 165, 780, 90, COLORS["user"], "Local Browser Session", ["访问 `web/App.jsx`、`web/Admin.jsx`、`web/Login.jsx`"]),
    Node("frontend", 90, 365, 570, 90, COLORS["frontend"], "Web Frontend", ["React + Vite 工作台；登录、审计主界面、管理后台"]),
    Node("webapi", 700, 365, 930, 90, COLORS["frontend"], "Frontend API Adapter", ["`web/api/*.js`、base URL fallback、鉴权请求、i18n/UI 状态"]),
    Node("fastapi", 90, 565, 610, 90, COLORS["backend"], "FastAPI App Factory", ["统一装配配置、数据库、Embedding、LLM 与 API 生命周期"]),
    Node("routers", 740, 565, 890, 90, COLORS["backend"], "Router & Control Surface", ["认证、合同、法规、税审、管理、健康检查与配置入口"]),
    Node("contracts", 90, 785, 350, 170, COLORS["domain"], "Contract Audit Services", ["文本抽取/OCR、证据检索、规则与记忆审计", "风险聚合、报告生成、导出回传"]),
    Node("regulations", 470, 785, 350, 170, COLORS["domain"], "Regulation Import & Search", ["法规上传、草稿解析、条文切分", "FTS/BM25 + 向量检索与索引迁移"]),
    Node("tax", 850, 785, 350, 170, COLORS["domain"], "Tax Audit Pipeline", ["法规解析、合同解析、匹配、问题生成", "运行态 session 与报告导出"]),
    Node("capabilities", 1230, 785, 400, 170, COLORS["domain"], "Capabilities / Agents / Memory Ops", ["技能/规则包、Agent Profile、记忆晋升", "管理配置与运行态能力编排"]),
    Node("ai_core", 90, 1115, 470, 170, COLORS["ai"], "AI Core Runtime", ["LLM Router、本地 main/small 模型路由", "JSON guard、fallback 与 provider 适配"]),
    Node("cognition", 590, 1115, 500, 170, COLORS["ai"], "Cognitive Services", ["Embedding、Reranker、Translation、OCR", "MinerU 接入与审计技能调用"]),
    Node("memory", 1120, 1115, 510, 170, COLORS["ai"], "Memory & RAG Orchestration", ["记忆索引/检索/验证与审计记忆流水线", "法规、语料、证据与检索协调"]),
    Node("data", 90, 1305, 540, 90, COLORS["data"], "Persistent Data Stores", ["SQLite app.db、FTS5、rag_zh/en.db、会话与导出元数据"]),
    Node("vector", 660, 1305, 450, 90, COLORS["data"], "Vector Store Layer", ["SQLite vector_chunks / FTS；可选 ChromaDB"]),
    Node("ops_core", 1140, 1305, 490, 90, COLORS["ops"], "Config / Deploy / Observability", ["部署脚本、配置文件、health、trace、日志与巡检"]),
    Node("llamacpp", 1790, 190, 250, 120, COLORS["external"], "llama.cpp Stack", ["主/小模型实例", "端口 18080 / 18081"]),
    Node("ollama", 2070, 190, 240, 120, COLORS["external"], "Optional Ollama", ["兼容 provider", "本地模型目录"]),
    Node("ocr", 1790, 360, 520, 120, COLORS["external"], "OCR & Parsing Toolchain", ["MinerU、Tesseract、Poppler、python-docx、pypdf、PIL"]),
    Node("files", 1790, 530, 520, 120, COLORS["external"], "Local Files / Models / Secrets", ["`data/`、`uploads`、`logs`、`models/llm`、OS keyring"]),
    Node("deploy", 1790, 700, 520, 120, COLORS["external"], "Desktop Deployment Runtime", ["资产下载、SHA256 校验、venv/npm、服务拉起", "离线白名单、端口探活、运行监控"]),
    Node("optional", 1790, 870, 520, 120, COLORS["external"], "Optional Scale Components", ["ChromaDB、Storybook/Test；Docker 文件仍在仓库但非主路径"]),
    Node("legend", 1790, 1460, 520, 130, COLORS["legend"], "Line Semantics", ["实线蓝色：同步调用/HTTP/API", "虚线橙色：异步处理/后台任务/启动控制", "点线灰色：配置、日志、健康检查、资产与文件依赖"]),
    Node("flow", 1790, 1610, 520, 90, COLORS["legend"], "Core Data Flow", ["1 上传合同/法规 -> 2 API/服务编排 -> 3 AI/OCR/检索", "4 写入 SQLite/向量层 -> 5 报告、配置与监控回传"]),
]


EDGES = [
    Edge("users", "bottom", "frontend", "top", "sync", "1"),
    Edge("ops", "bottom", "frontend", "top", "sync"),
    Edge("browser", "bottom", "webapi", "top", "sync"),
    Edge("frontend", "right", "webapi", "left", "sync"),
    Edge("webapi", "bottom", "routers", "top", "sync"),
    Edge("routers", "left", "fastapi", "right", "sync"),
    Edge("fastapi", "bottom", "contracts", "top", "sync", "2"),
    Edge("fastapi", "bottom", "regulations", "top", "sync"),
    Edge("fastapi", "bottom", "tax", "top", "sync"),
    Edge("fastapi", "bottom", "capabilities", "top", "sync"),
    Edge("contracts", "bottom", "ai_core", "top", "sync", "3"),
    Edge("contracts", "bottom", "cognition", "top", "sync"),
    Edge("contracts", "bottom", "memory", "top", "sync"),
    Edge("contracts", "bottom", "data", "top", "sync"),
    Edge("regulations", "bottom", "cognition", "top", "async"),
    Edge("regulations", "bottom", "memory", "top", "sync"),
    Edge("regulations", "bottom", "data", "top", "sync", "4"),
    Edge("regulations", "bottom", "vector", "top", "async"),
    Edge("tax", "bottom", "ai_core", "top", "sync"),
    Edge("tax", "bottom", "memory", "top", "sync"),
    Edge("tax", "bottom", "data", "top", "sync"),
    Edge("capabilities", "bottom", "memory", "top", "config"),
    Edge("capabilities", "bottom", "ops_core", "top", "config"),
    Edge("ai_core", "right", "cognition", "left", "sync"),
    Edge("cognition", "right", "memory", "left", "sync"),
    Edge("memory", "bottom", "vector", "top", "sync"),
    Edge("memory", "bottom", "data", "top", "sync"),
    Edge("ai_core", "bottom", "ops_core", "top", "config"),
    Edge("cognition", "bottom", "data", "top", "config"),
    Edge("ops_core", "left", "vector", "right", "config"),
    Edge("ops_core", "right", "deploy", "left", "async"),
    Edge("ops_core", "right", "files", "left", "config"),
    Edge("ai_core", "right", "llamacpp", "left", "sync"),
    Edge("ai_core", "right", "ollama", "left", "sync"),
    Edge("cognition", "right", "ocr", "left", "sync"),
    Edge("memory", "right", "optional", "left", "sync"),
    Edge("data", "right", "files", "left", "config"),
    Edge("vector", "right", "optional", "left", "sync"),
    Edge("deploy", "top", "llamacpp", "bottom", "async"),
    Edge("deploy", "bottom", "files", "top", "config"),
    Edge("routers", "right", "deploy", "left", "config"),
    Edge("ops", "right", "deploy", "left", "async", "5"),
]


def wrap_svg_line(text: str, width: int) -> list[str]:
    avg_chars = max(8, width // 14)
    pieces = []
    for paragraph in text.split("\n"):
        pieces.extend(textwrap.wrap(paragraph, width=avg_chars, break_long_words=False, break_on_hyphens=False) or [""])
    return pieces


def anchor(node: Node, side: str) -> tuple[int, int]:
    if side == "top":
        return node.x + node.w // 2, node.y
    if side == "bottom":
        return node.x + node.w // 2, node.y + node.h
    if side == "left":
        return node.x, node.y + node.h // 2
    if side == "right":
        return node.x + node.w, node.y + node.h // 2
    raise ValueError(f"Unsupported side: {side}")


def line_style(style: str) -> dict[str, str]:
    if style == "sync":
        return {"color": COLORS["sync"], "dash": "", "width": "4"}
    if style == "async":
        return {"color": COLORS["async"], "dash": "12 10", "width": "4"}
    return {"color": COLORS["config"], "dash": "3 10", "width": "3"}


def arrow_points(x1: float, y1: float, x2: float, y2: float, size: float = 14) -> list[tuple[float, float]]:
    angle = math.atan2(y2 - y1, x2 - x1)
    left = angle + math.pi - math.radians(28)
    right = angle + math.pi + math.radians(28)
    return [
        (x2, y2),
        (x2 + size * math.cos(left), y2 + size * math.sin(left)),
        (x2 + size * math.cos(right), y2 + size * math.sin(right)),
    ]


def rounded_rect_svg(x: int, y: int, w: int, h: int, rx: int, fill: str, stroke: str, stroke_width: int, opacity: float = 1.0) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"/>'
    )


def draw_title(draw: ImageDraw.ImageDraw) -> None:
    draw.text((50, 28), "AI Law Assistant - High Level Project Architecture", font=TITLE_FONT, fill=COLORS["title"])
    draw.text(
        (52, 78),
        "面向本地离线部署的法律审计平台：前端、后端、AI/RAG、数据、部署与外部依赖全景图",
        font=SUBTITLE_FONT,
        fill=COLORS["muted"],
    )


def draw_multiline_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: Iterable[str], font: ImageFont.ImageFont, fill: str, line_gap: int = 6) -> None:
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y = bbox[3] + line_gap


def draw_panel(draw: ImageDraw.ImageDraw, panel: Panel) -> None:
    shadow = (panel.x + 6, panel.y + 8, panel.x + panel.w + 6, panel.y + panel.h + 8)
    draw.rounded_rectangle(shadow, radius=24, fill=COLORS["shadow"])
    draw.rounded_rectangle(
        (panel.x, panel.y, panel.x + panel.w, panel.y + panel.h),
        radius=24,
        fill=COLORS["panel_fill"],
        outline=COLORS["panel_border"],
        width=2,
    )
    draw.text((panel.x + 24, panel.y + 18), panel.title, font=PANEL_TITLE_FONT, fill=COLORS["title"])
    draw.text((panel.x + 24, panel.y + 52), panel.subtitle, font=PANEL_SUBTITLE_FONT, fill=COLORS["muted"])


def draw_node(draw: ImageDraw.ImageDraw, node: Node) -> None:
    shadow = (node.x + 5, node.y + 7, node.x + node.w + 5, node.y + node.h + 7)
    draw.rounded_rectangle(shadow, radius=22, fill=COLORS["shadow"])
    draw.rounded_rectangle(
        (node.x, node.y, node.x + node.w, node.y + node.h),
        radius=22,
        fill=node.fill,
        outline=COLORS["panel_border"],
        width=2,
    )
    draw.text((node.x + 22, node.y + 16), node.title, font=NODE_TITLE_FONT, fill=COLORS["ink"])
    text_y = node.y + 54
    wrapped_lines: list[str] = []
    for raw in node.lines:
        wrapped_lines.extend(
            textwrap.wrap(
                raw,
                width=max(12, (node.w - 52) // 16),
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [""]
        )
    draw_multiline_text(draw, (node.x + 22, text_y), wrapped_lines, NODE_TEXT_FONT, COLORS["muted"])


def draw_edge(draw: ImageDraw.ImageDraw, lookup: dict[str, Node], edge: Edge) -> None:
    start = anchor(lookup[edge.start], edge.start_side)
    end = anchor(lookup[edge.end], edge.end_side)
    style = line_style(edge.style)
    color = style["color"]
    width = int(style["width"])
    dash = None if not style["dash"] else tuple(int(n) for n in style["dash"].split())
    points = [start]
    if edge.bend is not None:
        points.append(edge.bend)
    points.append(end)

    if dash:
        for a, b in zip(points, points[1:]):
            draw_dashed_line(draw, a, b, color, width, dash)
    else:
        draw.line(points, fill=color, width=width, joint="curve")

    if len(points) >= 2:
        arrow = arrow_points(points[-2][0], points[-2][1], points[-1][0], points[-1][1])
        draw.polygon(arrow, fill=color)

    if edge.label:
        midx = (start[0] + end[0]) // 2 if edge.bend is None else edge.bend[0]
        midy = (start[1] + end[1]) // 2 if edge.bend is None else edge.bend[1]
        badge = (midx - 18, midy - 16, midx + 18, midy + 16)
        draw.rounded_rectangle(badge, radius=10, fill=COLORS["white"], outline=color, width=2)
        draw.text((midx - 9, midy - 12), edge.label, font=LEGEND_FONT, fill=color)


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    fill: str,
    width: int,
    pattern: tuple[int, int],
) -> None:
    dash_len, gap_len = pattern
    total_len = math.dist(start, end)
    if total_len == 0:
        return
    dx = (end[0] - start[0]) / total_len
    dy = (end[1] - start[1]) / total_len
    dist = 0.0
    while dist < total_len:
        seg_end = min(dist + dash_len, total_len)
        x1 = start[0] + dx * dist
        y1 = start[1] + dy * dist
        x2 = start[0] + dx * seg_end
        y2 = start[1] + dy * seg_end
        draw.line((x1, y1, x2, y2), fill=fill, width=width)
        dist += dash_len + gap_len


def render_png() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), COLORS["bg"])
    draw = ImageDraw.Draw(img)

    draw_title(draw)
    for panel in PANELS:
        draw_panel(draw, panel)

    lookup = {node.key: node for node in NODES}
    for edge in EDGES:
        draw_edge(draw, lookup, edge)

    for node in NODES:
        draw_node(draw, node)

    img.save(PNG_PATH)


def render_svg() -> None:
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{COLORS["bg"]}"/>',
        '<style>',
        "text { font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; }",
        ".title { font-size: 42px; font-weight: 700; fill: #0f2740; }",
        ".subtitle { font-size: 22px; fill: #5f7387; }",
        ".panel-title { font-size: 24px; font-weight: 700; fill: #0f2740; }",
        ".panel-subtitle { font-size: 18px; fill: #5f7387; }",
        ".node-title { font-size: 25px; font-weight: 700; fill: #17324d; }",
        ".node-text { font-size: 19px; fill: #5f7387; }",
        ".badge-text { font-size: 18px; font-weight: 700; }",
        "</style>",
        '<defs><marker id="arrow-blue" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M 0 0 L 12 6 L 0 12 z" fill="#2b6cb0"/></marker>',
        '<marker id="arrow-orange" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M 0 0 L 12 6 L 0 12 z" fill="#d97706"/></marker>',
        '<marker id="arrow-gray" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M 0 0 L 12 6 L 0 12 z" fill="#6b7280"/></marker></defs>',
    ]
    out.append('<text x="50" y="60" class="title">AI Law Assistant - High Level Project Architecture</text>')
    out.append('<text x="52" y="98" class="subtitle">面向本地离线部署的法律审计平台：前端、后端、AI/RAG、数据、部署与外部依赖全景图</text>')

    for panel in PANELS:
        out.append(rounded_rect_svg(panel.x + 6, panel.y + 8, panel.w, panel.h, 24, COLORS["shadow"], "none", 0))
        out.append(rounded_rect_svg(panel.x, panel.y, panel.w, panel.h, 24, COLORS["panel_fill"], COLORS["panel_border"], 2))
        out.append(f'<text x="{panel.x + 24}" y="{panel.y + 42}" class="panel-title">{escape(panel.title)}</text>')
        out.append(f'<text x="{panel.x + 24}" y="{panel.y + 72}" class="panel-subtitle">{escape(panel.subtitle)}</text>')

    lookup = {node.key: node for node in NODES}
    for edge in EDGES:
        start = anchor(lookup[edge.start], edge.start_side)
        end = anchor(lookup[edge.end], edge.end_side)
        style = line_style(edge.style)
        marker = {
            "sync": "url(#arrow-blue)",
            "async": "url(#arrow-orange)",
            "config": "url(#arrow-gray)",
        }[edge.style]
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        path = f"M {start[0]} {start[1]} L {end[0]} {end[1]}"
        out.append(
            f'<path d="{path}" fill="none" stroke="{style["color"]}" stroke-width="{style["width"]}"{dash} marker-end="{marker}"/>'
        )
        if edge.label:
            midx = (start[0] + end[0]) // 2
            midy = (start[1] + end[1]) // 2
            out.append(rounded_rect_svg(midx - 18, midy - 16, 36, 32, 10, COLORS["white"], style["color"], 2))
            out.append(
                f'<text x="{midx}" y="{midy + 6}" text-anchor="middle" class="badge-text" fill="{style["color"]}">{escape(edge.label)}</text>'
            )

    for node in NODES:
        out.append(rounded_rect_svg(node.x + 5, node.y + 7, node.w, node.h, 22, COLORS["shadow"], "none", 0))
        out.append(rounded_rect_svg(node.x, node.y, node.w, node.h, 22, node.fill, COLORS["panel_border"], 2))
        out.append(f'<text x="{node.x + 22}" y="{node.y + 42}" class="node-title">{escape(node.title)}</text>')
        y = node.y + 72
        for raw in node.lines:
            for line in wrap_svg_line(raw, node.w - 46):
                out.append(f'<text x="{node.x + 22}" y="{y}" class="node-text">{escape(line)}</text>')
                y += 24

    out.append("</svg>")
    SVG_PATH.write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    render_svg()
    render_png()
