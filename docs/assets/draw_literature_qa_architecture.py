"""Render the documentation flowchart as a portable PNG (no Mermaid required)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math

ROOT = Path(__file__).resolve().parent
im = Image.new('RGB', (1800, 1600), '#FFFFFF')
d = ImageDraw.Draw(im)
FONT = 'C:/Windows/Fonts/msyh.ttc'

def text(x, y, value, size=30, fill='#18334A'):
    f = ImageFont.truetype(FONT, size)
    lines = value.split('\n')
    for i, line in enumerate(lines):
        d.text((x, y+i*(size+12)), line, font=f, fill=fill, anchor='mm')

def box(rect, title, subtitle='', color='#EAF2FA'):
    x0,y0,x1,y1=rect
    d.rounded_rectangle(rect, radius=18, fill=color, outline='#A4B8C8', width=2)
    text((x0+x1)/2, (y0+y1)/2-(22 if subtitle else 0), title, 31)
    if subtitle: text((x0+x1)/2,(y0+y1)/2+25,subtitle,24,'#476177')

def arrow(points, label=None, pos=None, color='#52738C'):
    d.line(points, fill=color, width=4, joint='curve')
    x,y=points[-1]; a,b=points[-2]
    angle=math.atan2(y-b,x-a)
    tri=[(x,y),(x-17*math.cos(angle-.45),y-17*math.sin(angle-.45)),(x-17*math.cos(angle+.45),y-17*math.sin(angle+.45))]
    d.polygon(tri,fill=color)
    if label: text(*pos,label,24,color)

text(900,60,'NeuroGRA 医学文献证据问答算法',44)
text(900,118,'由证据缺口决定下一步检索，无需训练模型',27,'#526A7D')
d.rounded_rectangle((45,175,1755,450),radius=25,fill='#F3F6F9')
text(190,210,'离线证据库',29)
box((85,265,425,385),'医学文献','解析原文与页码定位')
box((575,265,1045,385),'文本与向量索引','支持关键词与语义检索')
box((1195,265,1705,385),'可追溯证据图谱','实体 · 主张 · 条件 · 来源')
arrow([(425,325),(575,325)])
arrow([(255,385),(255,420),(1450,420),(1450,385)])

text(220,500,'在线问答与检索循环',29)
box((590,545,1110,635),'用户问题 → 证据需求','明确对象、比较维度与适用条件')
box((590,700,1110,790),'初始混合检索','从证据库获取候选原文')
box((590,865,1110,965),'证据池与状态更新','核验原文 · 比较条件 · 同源归组')
box((590,1040,1110,1140),'预算内证据组织','覆盖需求 · 去重 · 保留分歧双方')
for a,b in [(635,700),(790,865),(965,1040)]: arrow([(850,a),(850,b)])
arrow([(810,385),(810,470),(1160,470),(1160,745),(1110,745)])
arrow([(850,1140),(850,1190)])
box((620,1190,1080,1270),'最终上下文证据充分？',color='#E8F3ED')

box((90,865,450,1025),'按缺口选择动作','文本补检索 / 图谱扩展\n上下文补读 / 对照搜索',color='#FFF2DF')
arrow([(620,1230),(55,1230),(55,945),(90,945)],'不足且有预算',(285,1185),color='#B47D32')
arrow([(450,915),(590,915)],color='#B47D32')
text(270,1085,'记录成本，避免重复探索',23,'#8D662F')

box((1280,1180,1710,1280),'限定回答并列出缺口','预算耗尽或连续无进展',color='#FFF2DF')
arrow([(1080,1230),(1280,1230)])
box((590,1370,1110,1470),'生成回答并核验引用','回答 · 来源 · 条件 · 分歧 · 不足',color='#E8F3ED')
arrow([(850,1270),(850,1370)],'充分',(920,1320))
arrow([(1495,1280),(1495,1420),(1110,1420)])
text(900,1535,'所有检索动作访问同一版本证据库；图谱提供线索，原文支撑结论。',26)
im.save(ROOT/'NeuroGRA_医学文献证据问答架构.png')
