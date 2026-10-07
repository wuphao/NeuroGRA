"""Render the two explanatory figures for the proposed NeuroGRA method.

This draws original vector diagrams and PNG previews; it does not implement
the retrieval algorithm. Run from any directory using a Python with Pillow.
"""

from pathlib import Path
from math import hypot
from html import escape
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "graph_method_reconstruction"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
BOLD = Path("C:/Windows/Fonts/msyhbd.ttc")
INK = "#172B45"
MUTED = "#51667B"
BLUE = "#2563A6"
ORANGE = "#BD6A17"
GREEN = "#267A59"
GRAY = "#748291"


class Canvas:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.img = Image.new("RGB", (width, height), "white")
        self.draw = ImageDraw.Draw(self.img)
        self.svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="white"/>',
        ]
        self.text_bounds = []

    def text(self, x, y, text, size=24, color=INK, bold=False, anchor="mm"):
        font = ImageFont.truetype(str(BOLD if bold else FONT), size)
        self.draw.text((x, y), text, fill=color, font=font, anchor=anchor)
        bbox = self.draw.textbbox((x, y), text, font=font, anchor=anchor)
        self.text_bounds.append((text, bbox))
        svg_anchor = "middle" if anchor.startswith("m") else "start"
        self.svg.append(
            f'<text x="{x}" y="{y}" text-anchor="{svg_anchor}" '
            'dominant-baseline="central" font-family="Microsoft YaHei, sans-serif" '
            f'font-size="{size}" font-weight="{700 if bold else 400}" '
            f'fill="{color}">{escape(text)}</text>'
        )

    def rect(self, x, y, w, h, fill, stroke="#D7E1EB", radius=18, width=2):
        self.draw.rounded_rectangle((x, y, x + w, y + h), radius=radius,
                                    fill=fill, outline=stroke, width=width)
        self.svg.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'
        )

    def box(self, x, y, w, h, title, lines, color=BLUE, fill="#F2F7FC",
            title_size=26, body_size=23):
        self.rect(x, y, w, h, fill, color, radius=14)
        self.text(x + w / 2, y + 31, title, title_size, color, True)
        if lines:
            step = min(34, (h - 58) / len(lines))
            for i, line in enumerate(lines):
                self.text(x + w / 2, y + 64 + i * step, line, body_size)

    def arrow(self, points, color=BLUE, dashed=False, width=3):
        for p, q in zip(points[:-1], points[1:]):
            if dashed:
                dx, dy = q[0] - p[0], q[1] - p[1]
                length = hypot(dx, dy)
                if not length:
                    continue
                for a in range(0, int(length), 18):
                    b = min(a + 10, length)
                    self.draw.line((p[0] + a * dx / length, p[1] + a * dy / length,
                                    p[0] + b * dx / length, p[1] + b * dy / length),
                                   fill=color, width=width)
            else:
                self.draw.line((p, q), fill=color, width=width)
        p, q = points[-2], points[-1]
        dx, dy = q[0] - p[0], q[1] - p[1]
        length = hypot(dx, dy)
        ux, uy = dx / length, dy / length
        triangle = [q, (q[0] - 13 * ux + 6 * uy, q[1] - 13 * uy - 6 * ux),
                    (q[0] - 13 * ux - 6 * uy, q[1] - 13 * uy + 6 * ux)]
        self.draw.polygon(triangle, fill=color)
        path = " ".join(f"{x},{y}" for x, y in points)
        dash = ' stroke-dasharray="10 8"' if dashed else ""
        self.svg.append(f'<polyline points="{path}" fill="none" stroke="{color}" '
                        f'stroke-width="{width}" stroke-linejoin="round"{dash}/>')
        coords = " ".join(f"{x},{y}" for x, y in triangle)
        self.svg.append(f'<polygon points="{coords}" fill="{color}"/>')

    def save(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        for text, (left, top, right, bottom) in self.text_bounds:
            if left < 0 or top < 0 or right > self.width or bottom > self.height:
                raise ValueError(f"Text outside canvas: {text}")
        self.img.save(OUT / f"{name}.png")
        (OUT / f"{name}.svg").write_text("\n".join(self.svg + ["</svg>"]), encoding="utf-8")
        print(f"{name}: {self.width} x {self.height}, PNG + SVG")


def overview():
    c = Canvas(1600, 1140)
    c.text(800, 49, "面向图谱不完备的多模态证据检索与受约束探索", 38, bold=True)
    c.text(800, 93, "RAG-Anything 主体框架  ·  LightRAG 检索基础  ·  XoG 缺口探索", 24, MUTED)
    c.rect(25, 133, 1550, 285, "#FAFCFF")
    c.text(52, 162, "A  离线：构建多模态索引，并学习导航连接", 25, BLUE, True, "lm")
    c.box(52, 231, 235, 128, "固定版本文档", ["正文 / 图 / 表 / 公式", "位置与邻域上下文"], body_size=22)
    c.box(359, 193, 275, 85, "文本图", ["正文实体与关系"], body_size=22)
    c.box(359, 308, 275, 85, "跨模态图", ["描述 + 锚点 + 归属边"], body_size=22)
    c.box(717, 231, 273, 128, "领域实体对齐", ["融合检索视图", "关联三层来源记录"], body_size=22)
    c.box(1080, 193, 454, 200, "检索与探索索引", ["图谱 + 语义嵌入", "类型—关系观测统计", "ComplEx 图谱嵌入（需训练）", "仅学习允许使用的知识连接"], body_size=23)
    c.arrow([(287, 283), (322, 283), (322, 236), (359, 236)])
    c.arrow([(287, 305), (322, 305), (322, 351), (359, 351)])
    c.arrow([(634, 236), (677, 236), (677, 270), (717, 270)])
    c.arrow([(634, 351), (677, 351), (677, 320), (717, 320)])
    c.arrow([(990, 295), (1080, 295)])

    c.rect(25, 447, 1550, 607, "#FCFDFE")
    c.text(52, 480, "B  在线：先检索；必要需求未覆盖时，探索并核验", 25, BLUE, True, "lm")
    c.box(52, 555, 221, 130, "问题与证据需求", ["主题 / 框架 / 版本", "定义 / 关系 / 条件"], body_size=21, title_size=24)
    c.box(351, 555, 292, 130, "多模态混合检索", ["实体、关系两级检索", "正文与模态语义检索"], body_size=22)
    c.box(726, 555, 285, 130, "实际证据覆盖检查", ["来源核对 + 必要条件", "未满足项形成明确缺口"], body_size=22, title_size=25)
    c.box(1201, 555, 330, 130, "有引用的回答", ["需求覆盖 / 条件完整", "或预算结束后说明缺口"], color=GREEN, fill="#F0F8F3", body_size=22)
    c.arrow([(273, 620), (351, 620)])
    c.arrow([(643, 620), (726, 620)])
    c.arrow([(1011, 595), (1201, 595)], GREEN)
    c.text(1106, 565, "充分 / 停止", 20, GREEN)
    c.arrow([(1308, 418), (1308, 502), (497, 502), (497, 555)])
    c.text(1370, 461, "索引", 20, BLUE)

    c.box(84, 819, 277, 117, "关系候选", ["观测邻接 + 类型先验", "端点 / 方向 / 范围约束"], ORANGE, "#FFF7EC", body_size=21)
    c.box(433, 819, 277, 117, "已知实体候选", ["观测邻居 + KGE 排序", "只在已有向量中搜索"], ORANGE, "#FFF7EC", body_size=21)
    c.box(782, 819, 252, 117, "模型选择方向", ["语义筛选 / LLM 剪枝", "只选择给定候选 ID"], ORANGE, "#FFF7EC", body_size=21, title_size=24)
    c.box(1106, 819, 425, 117, "回到原文核验", ["正文 / 原图 / 表头脚注 / 定义", "对齐关系、来源及适用条件"], GREEN, "#F0F8F3", body_size=22)
    c.arrow([(868, 685), (868, 748), (222, 748), (222, 819)], ORANGE)
    c.text(524, 728, "仅探索尚未覆盖的必要需求", 22, ORANGE)
    c.arrow([(361, 878), (433, 878)], ORANGE, True)
    c.arrow([(710, 878), (782, 878)], ORANGE, True)
    c.arrow([(1034, 878), (1106, 878)], ORANGE, True)
    c.arrow([(1318, 819), (1318, 745), (1053, 745), (1053, 654), (1011, 654)], GREEN)
    c.text(1250, 727, "有来源的新增证据回流", 21, GREEN)
    c.arrow([(222, 936), (222, 1018), (1318, 1018), (1318, 936)], ORANGE, True)
    c.text(764, 995, "未入图实体：直接从原文发现与登记，绕过闭集 KGE", 23, ORANGE)
    c.text(800, 1100, "蓝色：主体索引与检索    橙色虚线：待核验候选    绿色：来源对齐后的证据；正式入图另走审核", 23, MUTED)
    c.save("overview")


def missing_cases():
    c = Canvas(1600, 1030)
    c.text(800, 48, "缺边与缺实体：恢复路径不同，来源约束相同", 38, bold=True)
    c.text(800, 94, "合成示例：问题要求解释框架 F 中临床模式 P 的时间限定 T", 24, MUTED)
    c.rect(35, 137, 748, 610, "#FAFCFF")
    c.rect(817, 137, 748, 610, "#FFFCF7")
    c.text(409, 174, "A  实体已存在，关系未观测", 28, BLUE, True)
    c.text(1191, 174, "B  原文有实体，图中未登记", 28, ORANGE, True)
    c.text(409, 218, "P、T 都有 KGE 向量", 22, MUTED)
    c.text(1191, 218, "T 没有 ID 与 KGE 向量", 22, MUTED)

    c.box(84, 267, 215, 116, "临床模式 P", ["已知实体", "ClinicalPattern"], body_size=21)
    c.box(520, 267, 215, 116, "时间限定 T", ["已知实体", "TemporalConstraint"], body_size=19)
    c.arrow([(299, 325), (520, 325)], ORANGE, True)
    c.text(409, 285, "缺失连接候选", 22, ORANGE)
    c.text(409, 361, "时间限定关系", 20, ORANGE)
    c.box(111, 429, 596, 107, "类型统计 + ComplEx + 模型剪枝", ["将 T 列为可能连接对象；分数不代表事实"], ORANGE, "#FFF7EC", title_size=25, body_size=21)
    c.arrow([(409, 383), (409, 429)], ORANGE, True)
    c.box(111, 594, 596, 109, "原文核验", ["定位支持 P—T 关系的片段 / 表格", "同时核对框架 F、表头、脚注与条件"], GREEN, "#F0F8F3", body_size=21)
    c.arrow([(409, 536), (409, 594)], ORANGE, True)
    c.text(590, 565, "预测连接 → 定向搜索", 20, ORANGE)

    c.box(867, 267, 215, 116, "临床模式 P", ["已知实体", "ClinicalPattern"], body_size=21)
    c.box(1302, 267, 215, 116, "时间限定 T", ["尚未入图", "不进入闭集预测"], GRAY, "#F2F4F6", body_size=21)
    c.arrow([(1082, 325), (1302, 325)], GRAY, True)
    c.text(1191, 285, "无法用现有 KGE", 21, GRAY)
    c.box(893, 429, 596, 107, "直接检索正文 / 表格 / 解释段落", ["发现 T 的定义、身份与 P—T 关系表达"], ORANGE, "#FFF7EC", title_size=25, body_size=21)
    c.arrow([(974, 383), (974, 406), (1191, 406), (1191, 429)], ORANGE)
    c.box(893, 594, 596, 109, "来源核验 + 临时实体登记", ["保留位置、关系、框架与条件", "发布后更新索引及 KGE"], GREEN, "#F0F8F3", body_size=21)
    c.arrow([(1191, 536), (1191, 594)], ORANGE, True)
    c.text(1372, 565, "原文发现 → 身份对齐", 20, ORANGE)

    c.text(800, 795, "关系状态必须区分", 26, bold=True)
    c.box(66, 838, 380, 105, "候选假设", ["只引导检索，不能支持答案"], ORANGE, "#FFF7EC", body_size=21)
    c.box(566, 838, 468, 105, "来源对齐 · 待审核", ["可引用原文陈述，保留提取状态"], GREEN, "#F0F8F3", body_size=21)
    c.box(1154, 838, 380, 105, "正式图谱知识", ["经既有审核流程发布"], BLUE, "#F2F7FC", body_size=21)
    c.arrow([(446, 890), (566, 890)], GREEN)
    c.arrow([(1034, 890), (1154, 890)], BLUE)
    c.text(506, 858, "核验", 20, GREEN)
    c.text(1094, 858, "审核", 20, BLUE)
    c.text(800, 993, "两种恢复都依赖原文支持；原文也缺失时，应保留缺口，不生成医学事实。", 24, MUTED)
    c.save("missing_cases")


if __name__ == "__main__":
    overview()
    missing_cases()
