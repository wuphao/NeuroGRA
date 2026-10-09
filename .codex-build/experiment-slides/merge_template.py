from pathlib import Path
from copy import deepcopy
import zipfile, re, json, hashlib
from lxml import etree as E

BUILD=Path(r'D:\Python Project\NeuroGRA\.codex-build\experiment-slides')
SOURCE=Path(r'D:\Python Project\NeuroGRA\docs\开题ppt.pptx')
BODY=BUILD/'body-draft.pptx'
OUT=BUILD/'candidate.pptx'
NS={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
def xml(b):return E.fromstring(b)
def dump(r):return E.tostring(r,xml_declaration=True,encoding='UTF-8',standalone=True)
def read(p):
    with zipfile.ZipFile(p) as z:return {n:z.read(n) for n in z.namelist()}
src=read(SOURCE); body=read(BODY); data=dict(src)
template=xml(src['ppt/slides/slide8.xml'])
template_rels=xml(src['ppt/slides/_rels/slide8.xml.rels'])
badge=xml(src['ppt/slides/slide3.xml']).find('p:cSld/p:spTree/p:pic',NS)
badge_rel_id=badge.find('.//a:blip',NS).get('{'+NS['r']+'}embed')
badge_rels=xml(src['ppt/slides/_rels/slide3.xml.rels'])
badge_target=next(r.get('Target') for r in badge_rels if r.get('Id')==badge_rel_id)
content_types=xml(src['[Content_Types].xml'])
def add_type(part,kind):
    if not any(t.get('PartName')=='/'+part for t in content_types):
        E.SubElement(content_types,'{'+CT+'}Override',PartName='/'+part,ContentType=kind)

parts=[8,9,10,12]
for idx,partno in enumerate(parts,1):
    root=deepcopy(template); tree=root.find('p:cSld/p:spTree',NS)
    brels=xml(body[f'ppt/slides/_rels/slide{idx}.xml.rels'])
    broot=xml(body[f'ppt/slides/slide{idx}.xml'])
    btree=broot.find('p:cSld/p:spTree',NS)
    rels=deepcopy(template_rels)
    for r in list(rels):
        if r.get('Type').endswith('/notesSlide'):rels.remove(r)
    E.SubElement(rels,'{'+REL+'}Relationship',Id='rIdBadge',Type=NS['r']+'/image',Target=badge_target)
    additions=[]
    for shape in list(btree)[2:]:
        sh=deepcopy(shape)
        words=''.join(sh.xpath('.//a:t/text()',namespaces=NS))
        if words in ['01','02','03','04','05']:
            xfrm=sh.find('p:spPr/a:xfrm',NS);off=xfrm.find('a:off',NS)
            pic=deepcopy(badge)
            pic.find('.//a:blip',NS).set('{'+NS['r']+'}embed','rIdBadge')
            bx=pic.find('p:spPr/a:xfrm',NS)
            bx.find('a:off',NS).set('x',str(int(off.get('x'))-15*9525))
            bx.find('a:off',NS).set('y',str(int(off.get('y'))-7*9525))
            bx.find('a:ext',NS).set('cx',str(64*9525));bx.find('a:ext',NS).set('cy',str(52*9525))
            additions.append(pic)
            for props in sh.xpath('.//a:rPr | .//a:defRPr | .//a:endParaRPr',namespaces=NS):
                for fill in props.findall('a:solidFill',NS):props.remove(fill)
                fill=E.Element('{'+NS['a']+'}solidFill')
                E.SubElement(fill,'{'+NS['a']+'}srgbClr',val='FFFFFF')
                props.insert(1 if props.find('a:ln',NS) is not None else 0,fill)
        additions.append(sh)
    # Reassign shape IDs and connector endpoints to avoid collisions with borrowed elements.
    native_id_map={}
    nextid=1
    for original in tree.xpath('.//p:cNvPr',namespaces=NS):
        original.set('id',str(nextid));nextid+=1
    for sh in additions:
        for c in sh.xpath('.//p:cNvPr',namespaces=NS):
            old=c.get('id');c.set('id',str(nextid))
            if sh.tag!='{'+NS['p']+'}pic':native_id_map[old]=str(nextid)
            nextid+=1
        tree.append(sh)
    for sh in additions:
        for c in sh.xpath('.//a:stCxn | .//a:endCxn',namespaces=NS):
            if c.get('id') in native_id_map:c.set('id',native_id_map[c.get('id')])
    # Preserve authored source notes with the original deck's notes master.
    nr=next((r for r in brels if r.get('Type').endswith('/notesSlide')),None)
    if nr is not None:
        target=nr.get('Target')
        genpart=target.lstrip('/') if target.startswith('/') else 'ppt/'+target.replace('../','')
        noteno=11+idx; note_part=f'ppt/notesSlides/notesSlide{noteno}.xml'
        data[note_part]=body[genpart]
        newnr=E.Element('{'+REL+'}Relationships',nsmap={None:REL})
        E.SubElement(newnr,'{'+REL+'}Relationship',Id='rId1',Type=NS['r']+'/notesMaster',Target='../notesMasters/notesMaster1.xml')
        E.SubElement(newnr,'{'+REL+'}Relationship',Id='rId2',Type=NS['r']+'/slide',Target=f'../slides/slide{partno}.xml')
        data[f'ppt/notesSlides/_rels/notesSlide{noteno}.xml.rels']=dump(newnr)
        E.SubElement(rels,'{'+REL+'}Relationship',Id='rIdExperimentNotes',Type=NS['r']+'/notesSlide',Target=f'../notesSlides/notesSlide{noteno}.xml')
        add_type(note_part,'application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml')
    data[f'ppt/slides/slide{partno}.xml']=dump(root)
    data[f'ppt/slides/_rels/slide{partno}.xml.rels']=dump(rels)
    add_type(f'ppt/slides/slide{partno}.xml','application/vnd.openxmlformats-officedocument.presentationml.slide+xml')

# Insert the fourth experiment page immediately before the unchanged thank-you slide.
presentation=xml(src['ppt/presentation.xml']);slides=presentation.find('p:sldIdLst',NS)
pres_rels=xml(src['ppt/_rels/presentation.xml.rels'])
E.SubElement(pres_rels,'{'+REL+'}Relationship',Id='rIdExperiment4',Type=NS['r']+'/slide',Target='slides/slide12.xml')
newid=max(int(s.get('id')) for s in slides)+1
sid=E.Element('{'+NS['p']+'}sldId',id=str(newid));sid.set('{'+NS['r']+'}id','rIdExperiment4')
slides.insert(10,sid)
data['ppt/presentation.xml']=dump(presentation)
data['ppt/_rels/presentation.xml.rels']=dump(pres_rels)

# Native table styles authored by Artifact Tool.
if 'ppt/tableStyles.xml' in body:
    if 'ppt/tableStyles.xml' in data:
        ts=xml(data['ppt/tableStyles.xml']);existing={x.get('styleId') for x in ts}
        for style in xml(body['ppt/tableStyles.xml']):
            if style.get('styleId') not in existing:ts.append(deepcopy(style))
        data['ppt/tableStyles.xml']=dump(ts)
    else:
        data['ppt/tableStyles.xml']=body['ppt/tableStyles.xml']
        E.SubElement(pres_rels,'{'+REL+'}Relationship',Id='rIdExperimentTableStyles',Type=NS['r']+'/tableStyles',Target='tableStyles.xml')
        data['ppt/_rels/presentation.xml.rels']=dump(pres_rels)
        add_type('ppt/tableStyles.xml','application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml')
data['[Content_Types].xml']=dump(content_types)
if 'docProps/app.xml' in data:
    app=xml(data['docProps/app.xml'])
    for el in app:
        if E.QName(el).localname=='Slides':el.text='12'
    data['docProps/app.xml']=dump(app)

with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED) as z:
    for n,b in data.items():z.writestr(n,b)
preserved=[f'ppt/slides/slide{i}.xml' for i in range(1,8)]+['ppt/slides/slide11.xml']
assert all(data[n]==src[n] for n in preserved)
print(json.dumps({'candidate':str(OUT),'slide_count':12,'experiment_slide_numbers':[8,9,10,11],'unchanged_source_slides':[1,2,3,4,5,6,7,11],'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest()},ensure_ascii=False))
