import os
import re
from collections import Counter
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from jinja2 import TemplateNotFound

# Leitura de arquivos
from pypdf import PdfReader
from docx import Document

# Banco de dados
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

# =========================
# Configuração do App
# =========================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads")
DB_PATH = os.path.join(INSTANCE_DIR, "lumen.db")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
ALLOWED_EXTS = {".pdf", ".docx", ".txt"}

# =========================
# Modelo do Banco de Dados
# =========================
class Analise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    titulo_resumo = db.Column(db.String(255))
    texto_original = db.Column(db.Text)
    tipo_peca = db.Column(db.String(100))

    def __repr__(self):
        return f"<Analise {self.id}>"

with app.app_context():
    db.create_all()

# =========================
# Glossário e Biblioteca (NÃO MEXER)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    # --- Legislação Fundamental ---
    {"key": "CF_HTML", "titulo": "Constituição Federal (Compilado)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},

    # --- Cursos Gratuitos (NOVO) ---
    {"key": "CURSO_STF", "titulo": "Cursos EAD – Supremo Tribunal Federal", "url": "https://ead.stf.jus.br/course/index.php?categoryid=3", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_ESA", "titulo": "ESA OAB – Cursos Gratuitos", "url": "https://esa.oab.org.br/home/ver-cursos?filter_categories_id%5B%5D=24", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_GOV", "titulo": "Escola Virtual Gov (EV.G) – Direito", "url": "https://www.escolavirtual.gov.br/catalogo", "tipo": "🎓 Curso Gratuito"},

    # --- Legislação Específica ---
    {"key": "CTN", "titulo": "Código Tributário Nacional", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Tributário"},
    {"key": "LIC", "titulo": "Lei de Licitações (14.133/21)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/L14133.htm", "tipo": "Administrativo"},
    {"key": "LIA", "titulo": "Lei de Improbidade Administrativa", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8429.htm", "tipo": "Administrativo"},
    {"key": "ECA", "titulo": "Estatuto da Criança e Adolescente", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm", "tipo": "Estatuto"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Penal Especial"},

    # --- Ferramentas ---
    {"key": "STF_GLOSS", "titulo": "Glossário Jurídico STF", "url": GLOSSARY_URL, "tipo": "Ferramenta"},
]

STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como","mais",
    "menos","já","não","sim","ser","foi","é","são","era","sendo","ter","tem","têm","haver","há",
    "art","artigo","lei","decreto","resolução","acórdão","relator","relatora","turma","câmara",
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso",
    "ementa","embargos","embargo","autos","vistos","juiz","juiza","excelencia", "vossa", "senhoria"
}

TERM_TRANSLATIONS = {
    "habeas corpus": "pedido para proteger a liberdade (contra prisão ilegal/abuso).",
    "periculum libertatis": "risco ligado à liberdade do acusado (perigo concreto de solto).",
    "fumus boni iuris": "aparência de bom direito (indícios de que o pedido faz sentido).",
    "periculum in mora": "risco da demora (se esperar, o direito pode se perder).",
    "ratio decidendi": "motivo central que sustentou a decisão (fundamento decisivo).",
    "obiter dictum": "comentário do julgador que não foi essencial para decidir.",
    "distinguishing": "diferenciar o caso do precedente por fatos distintos.",
    "overruling": "superação de entendimento anterior (mudança de jurisprudência).",
    "nulidade": "ato/processo inválido por violação de regra/garantia.",
    "ônus da prova": "quem tem o dever de provar determinado fato.",
    "tutela de urgência": "decisão rápida e provisória para evitar dano imediato.",
    "prisão preventiva": "prisão antes da sentença para proteger o processo/sociedade.",
    "trânsito em julgado": "quando não cabe mais recurso da decisão.",
    "in dubio pro reo": "na dúvida, decide-se a favor do réu.",
    "ex tunc": "efeito retroativo (vale desde o início).",
    "ex nunc": "efeito não retroativo (vale daqui para frente)."
}

# =========================
# ARTIGOS (SEPARADO DA BIBLIOTECA)
# - cada item informa "de onde é" e qual código se relaciona
# =========================
ARTICLE_DB = [
    {
        "titulo": "Precedentes obrigatórios e segurança jurídica no CPC/2015",
        "autores": "Daniel Mitidiero",
        "onde": "Revista de Processo (RT) / Doutrina processual (Brasil)",
        "ano": "2016",
        "codigo_relacionado": ["CPC"],
        "area": ["Processo Civil", "Precedentes"],
        "url": ""
    },
    {
        "titulo": "O sistema de precedentes no CPC/2015: fundamentos e desafios",
        "autores": "Fredie Didier Jr.",
        "onde": "Doutrina processual / artigos e capítulos sobre CPC/2015 (Brasil)",
        "ano": "2015-2018",
        "codigo_relacionado": ["CPC"],
        "area": ["Processo Civil", "Precedentes"],
        "url": ""
    },
    {
        "titulo": "Prisão preventiva e fundamentação: controle de legalidade e motivação concreta",
        "autores": "Aury Lopes Jr.",
        "onde": "Doutrina penal/processual penal (Brasil)",
        "ano": "2019-2023",
        "codigo_relacionado": ["CPP", "CF"],
        "area": ["Processo Penal", "Prisão"],
        "url": ""
    },
    {
        "titulo": "Responsabilidade civil: nexo causal, dano e critérios de quantificação",
        "autores": "Sérgio Cavalieri Filho",
        "onde": "Doutrina civil (Brasil)",
        "ano": "2010-2022",
        "codigo_relacionado": ["CC", "CF"],
        "area": ["Civil", "Danos"],
        "url": ""
    },
    {
        "titulo": "Dever de motivação das decisões judiciais e controle democrático",
        "autores": "Lenio Streck",
        "onde": "Doutrina constitucional e teoria do direito (Brasil)",
        "ano": "2014-2021",
        "codigo_relacionado": ["CF", "CPC", "CPP"],
        "area": ["Constitucional", "Decisões Judiciais"],
        "url": ""
    },
    {
        "titulo": "Tutela de urgência e requisitos: probabilidade do direito e perigo de dano",
        "autores": "Humberto Theodoro Júnior",
        "onde": "Doutrina processual civil (Brasil)",
        "ano": "2016-2022",
        "codigo_relacionado": ["CPC"],
        "area": ["Processo Civil", "Tutelas Provisórias"],
        "url": ""
    },
    {
        "titulo": "Vulnerabilidade e proteção do consumidor: fundamentos e aplicação jurisprudencial",
        "autores": "Cláudia Lima Marques",
        "onde": "Doutrina de direito do consumidor (Brasil)",
        "ano": "2000-2020",
        "codigo_relacionado": ["CDC", "CF"],
        "area": ["Consumidor"],
        "url": ""
    },
]

# =========================
# Funções de Texto (NLP e Regex)
# =========================
def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    # reduz linhas vazias demais
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text

def split_sentences(text: str):
    parts = re.split(r"(?<=[\.\?!])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]

def extract_block(text: str, start_patterns, stop_patterns, max_chars=4000):
    lower = (text or "").lower()
    start_idx = None
    for pat in start_patterns:
        m = re.search(pat, lower, flags=re.I | re.M)
        if m:
            start_idx = m.start()
            break
    if start_idx is None:
        return ""

    tail = text[start_idx:]
    tail_lower = lower[start_idx:]

    stop_idx = None
    for sp in stop_patterns:
        m = re.search(sp, tail_lower, flags=re.I | re.M)
        if m and m.start() > 0:
            stop_idx = m.start()
            break

    block = tail[:stop_idx] if stop_idx else tail
    return block.strip()[:max_chars].strip()

def pick_keywords(text: str, k=8):
    clean_text = re.sub(r"[^\w\s]", " ", (text or "").lower())
    tokens = [
        t for t in clean_text.split()
        if t not in STOPWORDS_PT and len(t) > 3 and not t.isdigit()
    ]
    if not tokens:
        return []
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(k)]

# =========================
# Extrações Jurídicas (melhoradas)
# =========================
_CODE_ALIASES = {
    "constituição federal": "CF",
    "cf/88": "CF",
    "cf": "CF",
    "código civil": "CC",
    "cc": "CC",
    "código penal": "CP",
    "cp": "CP",
    "código de processo penal": "CPP",
    "cpp": "CPP",
    "código de processo civil": "CPC",
    "cpc": "CPC",
    "clt": "CLT",
    "código de defesa do consumidor": "CDC",
    "cdc": "CDC",
    "ctn": "CTN",
}

def _normalize_code_label(label: str) -> str:
    l = (label or "").strip().lower()
    l = re.sub(r"\s+", " ", l)
    return _CODE_ALIASES.get(l, label.strip().upper())

def extract_legal_citations(text: str, limit=16):
    """
    Melhora: tenta capturar "art. X do CPC/CP/CF..." e "Lei n°..."
    Retorna itens "bonitos" para exibir.
    """
    t = text or ""
    out = []

    # Artigos com possível diploma ao lado
    # Ex.: art. 5º, CF; art 155 do CPP; artigo 186 do Código Civil
    art_pat = re.compile(
        r"\b(art\.?|artigo)\s*(\d{1,4})(?:\s*[º°])?"
        r"(?:\s*(?:,|do|da|dos|das)\s*([A-Za-zÀ-ÿ\/\.\s]{2,40}))?",
        flags=re.I
    )
    for m in art_pat.finditer(t):
        num = m.group(2)
        diploma_raw = (m.group(3) or "").strip()
        diploma = ""
        if diploma_raw:
            diploma_raw = re.sub(r"\s+", " ", diploma_raw)
            # corta se vier texto grande
            diploma_raw = diploma_raw[:40].strip(" ,.;:-")
            diploma = _normalize_code_label(diploma_raw)

        if diploma:
            out.append(f"art. {num} ({diploma})")
        else:
            out.append(f"art. {num}")

    # Leis / Decretos / Súmulas
    more_patterns = [
        r"\bLei\s+n[º°]?\s*[\d\.]+(?:/\d{2,4})?\b",
        r"\bDecreto\s+n[º°]?\s*[\d\.]+(?:/\d{2,4})?\b",
        r"\bSúmula\s*(?:Vinculante)?\s*n[º°]?\s*\d+\b",
        r"\bConstituição\s+Federal\b",
        r"\bCF/88\b",
        r"\bCPC\b|\bCPP\b|\bCP\b|\bCC\b|\bCLT\b|\bCDC\b|\bCTN\b",
    ]
    for pat in more_patterns:
        out.extend(re.findall(pat, t, flags=re.IGNORECASE))

    # Dedup mantendo ordem
    seen = set()
    unique = []
    for item in out:
        clean = normalize(item)
        clean = re.sub(r"\s+", " ", clean).strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            unique.append(clean)

    return unique[:limit]

def extract_jurisprudencia_refs(text: str, limit=14):
    patterns = [
        r"\bREsp\s*\d[\d\.\-\/]*\b",
        r"\bRE\s*\d[\d\.\-\/]*\b",
        r"\bARE\s*\d[\d\.\-\/]*\b",
        r"\bAgRg\b|\bAgInt\b|\bEDcl\b|\bEmbargos?\b",
        r"\bHC\s*\d[\d\.\-\/]*\b",
        r"\bRHC\s*\d[\d\.\-\/]*\b",
        r"\bADI\s*\d[\d\.\-\/]*\b|\bADPF\s*\d[\d\.\-\/]*\b|\bADC\s*\d[\d\.\-\/]*\b",
        r"\bTema\s*\d+\b",
        r"\bS[úu]mula\s*\d+\b"
    ]
    found = []
    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, text or "", flags=re.I):
            s = re.sub(r"\s+", " ", m.group(0).strip())
            k = s.lower()
            if s and k not in seen:
                found.append(s)
                seen.add(k)
            if len(found) >= limit:
                return found
    return found

def analyze_quality(text: str):
    t = (text or "").strip()
    warnings = []
    if len(t) < 450:
        warnings.append("Texto muito curto: a análise pode ficar genérica.")
    if len(t) > 150000:
        warnings.append("Texto muito grande: pode haver cortes no resumo.")
    return " ".join(warnings).strip()

def pick_best_question(text: str, fallback_base: str) -> str:
    candidates = [
        s for s in split_sentences(text)
        if s.endswith("?") and 15 <= len(s) <= 240
    ]
    if candidates:
        return candidates[0].strip()
    return "Qual é a controvérsia jurídica principal deste caso?"

def extract_terms_translation(text: str, max_items: int = 10) -> list[dict]:
    t = (text or "").lower()
    hits = []
    seen = set()
    for term, tr in TERM_TRANSLATIONS.items():
        if term in t and term not in seen:
            seen.add(term)
            hits.append({"termo": term, "traducao": tr})
            if len(hits) >= max_items:
                break
    return hits

# =========================
# Detecção do "caso" (sintaxe / metadados)
# =========================
def detect_case_metadata(text: str) -> dict:
    """
    Tenta detectar:
    - tribunal provável (STF/STJ/TJ/TRF)
    - classe processual (HC, REsp, RE, etc.)
    - número do processo (quando houver)
    - relator(a)
    - datas
    """
    t = text or ""
    low = t.lower()

    # tribunal (heurística)
    tribunal = None
    if "supremo tribunal federal" in low or re.search(r"\bstf\b", low):
        tribunal = "STF"
    elif "superior tribunal de justiça" in low or re.search(r"\bstj\b", low):
        tribunal = "STJ"
    elif re.search(r"\btrf-?\s*\d\b", low) or "tribunal regional federal" in low:
        tribunal = "TRF"
    elif re.search(r"\btj\w{2}\b", low) or "tribunal de justiça" in low:
        tribunal = "TJ"
    else:
        tribunal = ""

    # classe e número (bem comum em capas/ementas)
    classe = ""
    numero = ""
    m = re.search(r"\b(HC|RHC|REsp|RE|ARE|ADI|ADPF|ADC)\b\s*(n[º°]\s*)?(\d[\d\.\-\/]*)", t, flags=re.I)
    if m:
        classe = m.group(1).upper()
        numero = re.sub(r"\s+", "", m.group(3))

    # CNJ (0000000-00.0000.0.00.0000)
    mcnj = re.search(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b", t)
    numero_cnj = mcnj.group(0) if mcnj else ""

    # relator(a)
    relator = ""
    mrel = re.search(r"\brelator(?:a)?\s*:\s*([A-ZÀ-Ÿ][A-Za-zÀ-ÿ\.\s]{3,60})", t, flags=re.I)
    if mrel:
        relator = mrel.group(1).strip()
        relator = re.sub(r"\s{2,}", " ", relator)

    # datas (julgamento/publicação)
    datas = []
    for pat in [
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
        r"\b(\d{1,2}\s+de\s+[A-Za-zÀ-ÿ]+\s+de\s+\d{4})\b"
    ]:
        for m in re.finditer(pat, t, flags=re.I):
            ds = re.sub(r"\s+", " ", m.group(1)).strip()
            if ds not in datas:
                datas.append(ds)
            if len(datas) >= 4:
                break

    # tipo de peça (heurística simples)
    tipo = ""
    if re.search(r"\bementa\b", low) and re.search(r"\bac[oó]rd[aã]o\b", low):
        tipo = "Acórdão"
    elif re.search(r"\bsenten[cç]a\b", low):
        tipo = "Sentença"
    elif re.search(r"\bpeti[cç][aã]o\b", low) or re.search(r"\binicial\b", low):
        tipo = "Petição"
    elif re.search(r"\bdecis[aã]o\b", low):
        tipo = "Decisão"
    else:
        tipo = "Documento"

    return {
        "tribunal": tribunal,
        "classe": classe,
        "numero": numero,
        "numero_cnj": numero_cnj,
        "relator": relator,
        "datas_mencionadas": datas,
        "tipo_peca_detectado": tipo
    }

def extract_dispositivo(text: str) -> str:
    """
    Tenta pegar o resultado final (dispositivo).
    """
    t = normalize(text)
    bloco = extract_block(
        t,
        start_patterns=[r"\bdispositivo\b", r"\bdecido\b", r"\bisto posto\b"],
        stop_patterns=[r"\bpublique-se\b", r"\bintime-se\b", r"\btransitado\b", r"\bac[oó]rd[aã]o\b"],
        max_chars=1800
    )
    if bloco:
        # limpa cabeçalho repetitivo
        bloco = re.sub(r"^\s*(dispositivo|decido|isto posto)\s*[:\-]?\s*", "", bloco, flags=re.I)
        return bloco.strip()
    return ""

def infer_codes_from_text(text: str, citations: list[str]) -> list[str]:
    """
    Pega "CPC/CPP/CP/CC/CF..." com base em citações e menções.
    """
    low = (text or "").lower()
    codes = set()

    # de citações do tipo "art. X (CPC)"
    for c in citations or []:
        m = re.search(r"\((CF|CPC|CPP|CP|CC|CLT|CDC|CTN)\)", c, flags=re.I)
        if m:
            codes.add(m.group(1).upper())

    # menções diretas no texto
    for label, code in _CODE_ALIASES.items():
        if label in low:
            codes.add(code)

    # abreviações soltas
    for code in ["CF", "CPC", "CPP", "CP", "CC", "CLT", "CDC", "CTN"]:
        if re.search(rf"\b{code}\b", text or "", flags=re.I):
            codes.add(code)

    # ordem “útil”
    order = ["CF", "CPC", "CPP", "CP", "CC", "CDC", "CLT", "CTN"]
    return [c for c in order if c in codes]

def infer_area_from_codes(codes: list[str], keywords: list[str]) -> str:
    """
    Um rótulo simples do ramo predominante.
    """
    cset = set(codes or [])
    kset = set((keywords or []))

    if "CPP" in cset or "CP" in cset or any(k in kset for k in ["prisao", "preventiva", "habeas", "penal"]):
        return "Processo Penal / Penal"
    if "CPC" in cset or any(k in kset for k in ["tutela", "urgencia", "apelação", "agravo", "sentenca"]):
        return "Processo Civil"
    if "CC" in cset or any(k in kset for k in ["responsabilidade", "indenizacao", "dano", "moral"]):
        return "Direito Civil"
    if "CDC" in cset or any(k in kset for k in ["consumidor", "fornecedor", "vicio"]):
        return "Direito do Consumidor"
    if "CLT" in cset or any(k in kset for k in ["trabalho", "empregado", "verbas"]):
        return "Direito do Trabalho"
    if "CTN" in cset or any(k in kset for k in ["tributo", "icms", "ipi", "credito"]):
        return "Direito Tributário"
    if "CF" in cset:
        return "Constitucional"
    return "Geral"

def recommend_articles(codes: list[str], area: str, max_items: int = 6) -> list[dict]:
    """
    Filtra ARTICLE_DB por códigos e/ou área.
    """
    codes = codes or []
    out = []

    for a in ARTICLE_DB:
        ok_code = any(c in (a.get("codigo_relacionado") or []) for c in codes) if codes else False
        ok_area = any(area_part.lower() in (area or "").lower() for area_part in (a.get("area") or []))
        # aceita se bater por código OU por área
        if ok_code or ok_area:
            out.append(a)

    # fallback: se nada casar, devolve alguns gerais com CF/CPC/CPP
    if not out:
        for a in ARTICLE_DB:
            if any(c in (a.get("codigo_relacionado") or []) for c in ["CF", "CPC", "CPP"]):
                out.append(a)

    # dedup por título
    seen = set()
    uniq = []
    for a in out:
        k = (a.get("titulo") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(a)
        if len(uniq) >= max_items:
            break

    return uniq

def build_search_queries(pergunta: str, ementa: str, keywords: list[str], tribunal_hint: str = "", max_items: int = 5) -> list[str]:
    kws = [k for k in (keywords or []) if k and len(k) >= 4][:4]
    out = []

    # 1) query por palavras-chave
    if kws:
        out.append(" AND ".join([f'"{k}"' for k in kws]))

    # 2) pergunta + palavras centrais
    if pergunta:
        base = pergunta
        if kws:
            base += " " + " ".join(kws[:2])
        out.append(base.strip())

    # 3) jurisprudência (conforme tribunal sugerido)
    th = (tribunal_hint or "").upper().strip()
    if th == "STJ":
        out.append(f"site:stj.jus.br {' '.join(kws[:2])}".strip())
    elif th == "STF":
        out.append(f"site:stf.jus.br {' '.join(kws[:2])}".strip())
    else:
        # genérico (sem “mexer” na biblioteca)
        out.append(f"jurisprudência {' '.join(kws[:3])}".strip())

    # 4) acadêmico (sem link; é só query pronta)
    if kws:
        out.append(f'pesquisa acadêmica {kws[0]} {kws[1] if len(kws)>1 else ""} CPC CPP CP CC'.strip())

    # dedup
    seen = set()
    uniq = []
    for q in out:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
        if len(uniq) >= max_items:
            break

    return uniq

def build_action_checklist(text: str) -> list[str]:
    low = (text or "").lower()
    items = []

    if "prisão" in low or "hc" in low or "liberdade" in low:
        items.extend([
            "Verificar se há fundamentação concreta (não genérica).",
            "Checar contemporaneidade dos fatos (os fatos são recentes?).",
            "Analisar excesso de prazo e adequação de medidas cautelares diversas."
        ])
    if "dano moral" in low or "indeniza" in low:
        items.extend([
            "Verificar nexo causal (ligação entre conduta e dano).",
            "Analisar critérios do quantum indenizatório (proporcionalidade/razoabilidade).",
            "Checar excludentes de responsabilidade e prova do dano."
        ])
    if "recurso" in low or "apelação" in low or "agravo" in low:
        items.extend([
            "Verificar tempestividade (prazo do recurso).",
            "Checar preparo (custas/porte, quando aplicável).",
            "Conferir prequestionamento (se matéria foi enfrentada antes, quando exigido)."
        ])

    if not items:
        items = [
            "Identificar a controvérsia central (ponto que decide o caso).",
            "Listar fatos relevantes cronologicamente (o que ocorreu e quando).",
            "Isolar fundamentos determinantes (ratio decidendi) vs. comentários acessórios.",
            "Checar o dispositivo (resultado) e efeitos práticos."
        ]
    return items

def suggest_library_links(text: str, max_items: int = 7):
    t = (text or "").lower()
    out = []

    for link in LIBRARY_LINKS:
        keywords = link["titulo"].split()
        matches = sum(1 for k in keywords if len(k) > 3 and k.lower() in t)
        if matches >= 1:
            out.append(link)

    if not out:
        out = [l for l in LIBRARY_LINKS if l["key"] in ["CF_HTML", "CPC", "STF_GLOSS"]]

    if "oab" in t or "exame" in t:
        try:
            out.append([l for l in LIBRARY_LINKS if l["key"] == "CURSO_ESA"][0])
        except Exception:
            pass

    seen = set()
    unique_out = []
    for x in out:
        if x["key"] not in seen:
            unique_out.append(x)
            seen.add(x["key"])

    return unique_out[:max_items]

# =========================
# "Sintaxe do caso" (síntese)
# =========================
def build_case_syntax(text: str, ementa: str, dispositivo: str, pergunta: str, citations: list[str], jurisrefs: list[str]) -> dict:
    """
    Um resumo estruturado para ficar mais útil do que um texto solto.
    """
    fatos = ""
    # tenta extrair "relatório" ou trecho inicial (muito comum)
    rel = extract_block(
        text,
        start_patterns=[r"\brelat[oó]rio\b", r"\bdos fatos\b", r"\bfatos\b"],
        stop_patterns=[r"\bfundamenta[cç][aã]o\b", r"\bdireito\b", r"\bdecido\b", r"\bdispositivo\b"],
        max_chars=1200
    )
    if rel:
        fatos = rel
    else:
        # fallback: 3-5 frases iniciais da ementa/texto
        sents = split_sentences(ementa or text[:1200])
        fatos = " ".join(sents[:4]).strip()

    fundamentos = []
    if citations:
        fundamentos.append("Base normativa citada: " + ", ".join(citations[:8]))
    if jurisrefs:
        fundamentos.append("Referências de jurisprudência: " + ", ".join(jurisrefs[:6]))

    # Resultado/dispositivo em linguagem simples
    resultado = dispositivo.strip()
    if not resultado:
        # tenta “nego provimento / dou provimento / concedo a ordem”
        mres = re.search(r"\b(concedo a ordem|denego a ordem|dou provimento|nego provimento|julgo procedente|julgo improcedente)\b", text, flags=re.I)
        if mres:
            resultado = mres.group(1).strip().capitalize()

    return {
        "o_que_e": "Síntese estruturada do caso (fatos → controvérsia → fundamentos → resultado).",
        "fatos_relevantes": fatos[:1200].strip(),
        "controversia": pergunta,
        "fundamentos": fundamentos,
        "resultado_dispositivo": (resultado[:1600].strip() if resultado else "Não foi possível isolar um dispositivo claro no texto enviado.")
    }

# =========================
# Lógica Principal
# =========================
def build_output(text: str):
    text = normalize(text)

    # Ementa
    ementa = extract_block(
        text,
        [r"\bementa\b"],
        [r"\bac[oó]rd[aã]o\b", r"\brelat[oó]rio\b"],
        2000
    ) or text[:1100]

    # Keywords
    base_for_keywords = ementa or text[:1500]
    keywords = pick_keywords(base_for_keywords, k=9)

    # Pergunta
    pergunta = pick_best_question(text, base_for_keywords)

    # Extrações Jurídicas
    fundamentos_normas = extract_legal_citations(text, limit=18)
    fundamentos_juris = extract_jurisprudencia_refs(text, limit=14)

    # Dispositivo
    dispositivo = extract_dispositivo(text)

    # Metadados do caso
    meta = detect_case_metadata(text)

    # Códigos e área
    codes = infer_codes_from_text(text, fundamentos_normas)
    area = infer_area_from_codes(codes, keywords)

    # Resumo “melhor”
    resumo = (ementa or "")[:760] + ("..." if len(ementa or "") > 760 else "")

    # Queries (agora com hint de tribunal)
    pesquisas = build_search_queries(pergunta, ementa, keywords, tribunal_hint=meta.get("tribunal", ""))

    # Checklist + sugestões biblioteca
    checklist = build_action_checklist(text)
    sugestoes = suggest_library_links(text)
    termos_importantes = extract_terms_translation(text)

    # Artigos recomendados (com “de onde são” e código relacionado)
    artigos = recommend_articles(codes, area, max_items=6)

    # Sintaxe do caso (novo)
    sintaxe_caso = build_case_syntax(
        text=text,
        ementa=ementa,
        dispositivo=dispositivo,
        pergunta=pergunta,
        citations=fundamentos_normas,
        jurisrefs=fundamentos_juris
    )

    # Tema principal mais “bonito”
    head = keywords[:3]
    tema_principal = f"Análise: {', '.join([w.title() for w in head])}" if head else "Análise Jurídica"

    return {
        "tema_principal": tema_principal,
        "area_sugerida": area,
        "codigos_relacionados": codes,

        "meta": meta,  # tribunal, classe, número, relator etc.
        "sintaxe_caso": sintaxe_caso,

        "pergunta": pergunta,
        "fundamentos_normas": fundamentos_normas,
        "fundamentos_juris": fundamentos_juris,
        "keywords": keywords,
        "queries_juris": pesquisas,
        "checklist": checklist,
        "resumo": resumo,
        "dispositivo": dispositivo,

        "alerta": analyze_quality(text),
        "sugestoes": sugestoes,
        "termos_importantes": termos_importantes,

        "artigos_recomendados": artigos,  # novo
        "glossario_source": GLOSSARY_URL,
    }

# =========================
# Upload Helpers
# =========================
def allowed_file(filename):
    return os.path.splitext((filename or "").lower())[1] in ALLOWED_EXTS

def get_text_from_upload(file):
    filename = secure_filename(file.filename or "")
    if not filename:
        return ""

    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(UPLOAD_DIR, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
    file.save(path)

    text = ""
    try:
        if ext == ".pdf":
            reader = PdfReader(path)
            # Evita quebra se alguma página falhar
            parts = []
            for p in reader.pages:
                try:
                    parts.append(p.extract_text() or "")
                except Exception:
                    parts.append("")
            text = "\n".join(parts)
        elif ext == ".docx":
            doc = Document(path)
            text = "\n".join([p.text for p in doc.paragraphs if (p.text or "").strip()])
        elif ext == ".txt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
    except Exception as e:
        print(f"Erro ao ler arquivo: {e}")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

    return text

# =========================
# Rotas
# =========================
@app.route("/")
def home():
    historico = Analise.query.order_by(Analise.data_criacao.desc()).limit(5).all()
    return render_template("index.html", historico=historico)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto = (request.form.get("texto") or "").strip()
    arquivo = request.files.get("arquivo")

    if arquivo and arquivo.filename:
        if not allowed_file(arquivo.filename):
            flash("Envie apenas PDF, DOCX ou TXT.", "error")
            return redirect(url_for("home"))
        extraido = get_text_from_upload(arquivo)
        texto = f"{texto}\n\n{extraido}".strip()

    if not texto or len(texto) < 10:
        flash("O documento está vazio ou muito curto.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)

    nova = Analise(
        titulo_resumo=out["tema_principal"],
        texto_original=texto,
        tipo_peca=out.get("meta", {}).get("tipo_peca_detectado", "Documento Jurídico")
    )
    db.session.add(nova)
    db.session.commit()

    return render_template(
        "resultado.html",
        out=out,
        texto=texto,
        now=datetime.now(),
        analise_id=nova.id
    )

@app.route("/resultado/<int:id>")
def resultado(id):
    analise = Analise.query.get_or_404(id)
    out = build_output(analise.texto_original)
    return render_template("resultado.html", out=out, texto=analise.texto_original, now=datetime.now(), analise_id=analise.id)

@app.route("/historico")
def historico():
    page = request.args.get("page", 1, type=int)
    analises = Analise.query.order_by(Analise.data_criacao.desc()).paginate(page=page, per_page=10)
    return render_template("historico.html", paginacao=analises)

@app.route("/excluir/<int:id>")
def excluir(id):
    analise = Analise.query.get_or_404(id)
    db.session.delete(analise)
    db.session.commit()
    flash("Análise removida.", "success")
    return redirect(url_for("home"))

@app.get("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.get("/glossario")
def glossario():
    return redirect(GLOSSARY_URL)

@app.get("/sobre")
def sobre():
    return render_template("sobre.html")

# Erros
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
