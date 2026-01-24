import os
import re
from collections import Counter
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from jinja2 import TemplateNotFound

# Leitura de arquivos
from pypdf import PdfReader
from docx import Document

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")

# =========================
# Upload config
# =========================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB
ALLOWED_EXTS = {".pdf", ".docx"}

# =========================
# Glossário STF (link oficial)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

# =========================
# Biblioteca (base) — usada no /biblioteca e nas sugestões
# =========================
LIBRARY_LINKS = [
    # Constituição
    {"key": "CF_PDF", "titulo": "Constituição Federal (PDF – DOU)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/DOUconstituicao88.pdf", "tipo": "Constituição"},
    {"key": "CF_HTML", "titulo": "Constituição Federal (texto compilado)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},

    # Códigos fundamentais
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Código • Trabalho"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor (CDC)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Código • Consumidor"},
    {"key": "CTN", "titulo": "Código Tributário Nacional (CTN)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Código • Tributário"},

    # Administrativo
    {"key": "LIC", "titulo": "Lei de Licitações e Contratos (Lei 14.133/2021)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/L14133.htm", "tipo": "Lei Administrativa"},
    {"key": "LPA", "titulo": "Lei do Processo Administrativo (Lei 9.784/1999)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l9784.htm", "tipo": "Lei Administrativa"},
    {"key": "LIA", "titulo": "Lei de Improbidade Administrativa (Lei 8.429/1992)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8429.htm", "tipo": "Lei Administrativa"},

    # Previdenciário
    {"key": "LBPS", "titulo": "Lei de Benefícios da Previdência (Lei 8.213/1991)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8213cons.htm", "tipo": "Lei Previdenciária"},

    # Eleitoral
    {"key": "CE", "titulo": "Código Eleitoral", "url": "https://www.planalto.gov.br/ccivil_03/leis/l4737.htm", "tipo": "Código • Eleitoral"},
    {"key": "LEI_ELEICOES", "titulo": "Lei das Eleições (Lei 9.504/1997)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l9504.htm", "tipo": "Lei Eleitoral"},

    # Família / criança / proteção
    {"key": "ECA", "titulo": "Estatuto da Criança e do Adolescente (ECA)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm", "tipo": "Estatuto"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha (Lei 11.340/2006)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Lei Especial"},
    {"key": "IDOSO", "titulo": "Estatuto do Idoso (Lei 10.741/2003)", "url": "https://www.planalto.gov.br/ccivil_03/leis/2003/l10.741.htm", "tipo": "Estatuto"},

    # Direitos humanos
    {"key": "PSSJ", "titulo": "Pacto de San José da Costa Rica (Decreto 678/1992)", "url": "https://www.planalto.gov.br/ccivil_03/decreto/d0678.htm", "tipo": "Tratado Internacional"},

    # Portais e bibliotecas
    {"key": "PORTAL_PLANALTO", "titulo": "Portal da Legislação – Planalto", "url": "https://www4.planalto.gov.br/legislacao/portal-legis", "tipo": "Portal Oficial"},
    {"key": "LIVROS_ABERTOS", "titulo": "Livros Abertos – Direito (acesso aberto)", "url": "https://www.livrosabertos.abcd.usp.br/portaldelivrosUSP/catalog/category/direito", "tipo": "Livros acadêmicos"},
    {"key": "OAB", "titulo": "Biblioteca Digital da OAB", "url": "http://www.oab.org.br/biblioteca-digital/publicacoes#", "tipo": "OAB"},

    # Glossário STF
    {"key": "GLOSS_STF", "titulo": "Glossário jurídico – STF", "url": GLOSSARY_URL, "tipo": "Glossário"},
]

# =========================
# Linguagem / stopwords
# =========================
STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como","mais",
    "menos","já","não","sim","ser","foi","é","são","era","sendo","ter","tem","têm","haver","há",
    "art","artigo","lei","decreto","resolução","acórdão","relator","relatora","turma","câmara",
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso",
    "ementa"
}

# =========================
# Termos jurídicos (tradução simples)
# =========================
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
    "prisão preventiva": "prisão antes da sentença para proteger o processo/sociedade (com fundamento).",
}

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
# Utilidades de texto
# =========================
def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def split_sentences(text: str):
    parts = re.split(r"(?<=[\.\?!])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]

def extract_block(text: str, start_patterns, stop_patterns, max_chars=4000):
    lower = text.lower()
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
    tokens = re.findall(r"[A-Za-zÀ-ÿ]{3,}", (text or "").lower())
    tokens = [t for t in tokens if t not in STOPWORDS_PT]
    if not tokens:
        return []
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(k)]

# ✅ Normas/artigos com ORIGEM (CP/CPP/CPC/CF etc.)
def extract_legal_citations(text: str, limit=14):
    t = text or ""

    CODE_NAME = {
        "CF": "Constituição Federal (CF/88)",
        "CF/88": "Constituição Federal (CF/88)",
        "CP": "Código Penal (CP)",
        "CPP": "Código de Processo Penal (CPP)",
        "CPC": "Código de Processo Civil (CPC)",
        "CLT": "CLT",
        "CDC": "Código de Defesa do Consumidor (CDC)",
        "CTN": "Código Tributário Nacional (CTN)",
    }

    global_codes = []
    for code in ["CF/88", "CF", "CPP", "CP", "CPC", "CLT", "CDC", "CTN"]:
        if re.search(rf"\b{re.escape(code)}\b", t):
            if code not in global_codes:
                global_codes.append(code)

    if re.search(r"c[oó]digo\s+penal", t, flags=re.I) and "CP" not in global_codes:
        global_codes.append("CP")
    if re.search(r"c[oó]digo\s+de\s+processo\s+penal", t, flags=re.I) and "CPP" not in global_codes:
        global_codes.append("CPP")
    if re.search(r"c[oó]digo\s+de\s+processo\s+civil", t, flags=re.I) and "CPC" not in global_codes:
        global_codes.append("CPC")
    if re.search(r"constitui[cç][aã]o\s+federal", t, flags=re.I) and "CF/88" not in global_codes:
        global_codes.append("CF/88")

    art_pat = re.compile(
        r"\bart\.?\s*(\d+[a-zA-Zº°]*)"
        r"(?:(?:\s*,\s*§\s*\d+º?)|(?:\s*,\s*inc\.\s*[ivxlcdm]+)|(?:\s*,\s*[IVXLCDM]+))*",
        flags=re.I
    )

    def infer_code_by_window(start: int, end: int) -> str:
        window = t[max(0, start-140): min(len(t), end+140)]
        w = window.lower()

        if re.search(r"\bcpp\b", w): return "CPP"
        if re.search(r"\bcp\b", w): return "CP"
        if re.search(r"\bcpc\b", w): return "CPC"
        if re.search(r"\bclt\b", w): return "CLT"
        if re.search(r"\bcdc\b", w): return "CDC"
        if re.search(r"\bctn\b", w): return "CTN"
        if re.search(r"\bcf/88\b|\bcf\b", w): return "CF/88"

        if "código penal" in w: return "CP"
        if "processo penal" in w: return "CPP"
        if "processo civil" in w: return "CPC"
        if "constituição federal" in w: return "CF/88"

        if len(global_codes) == 1:
            return global_codes[0]

        return ""

    found = []
    found_low = set()

    for m in art_pat.finditer(t):
        raw = re.sub(r"\s+", " ", m.group(0)).strip()
        code = infer_code_by_window(m.start(), m.end())
        pretty = f"{raw} — {CODE_NAME.get(code, code)}" if code else raw

        key = pretty.lower()
        if key not in found_low:
            found.append(pretty)
            found_low.add(key)
        if len(found) >= limit:
            break

    extra_patterns = [
        r"\blei\s*n[ºo]\s*\d[\d\.\-]*\s*(?:/|\s*de\s*)\s*\d{2,4}\b",
        r"\bdecreto-lei\s*n[ºo]\s*\d[\d\.\-]*\b",
    ]
    for pat in extra_patterns:
        for m in re.finditer(pat, t, flags=re.I):
            s = re.sub(r"\s+", " ", m.group(0).strip())
            if s and s.lower() not in found_low:
                found.append(s)
                found_low.add(s.lower())
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break

    if not found:
        for code in global_codes[:3]:
            label = CODE_NAME.get(code, code)
            if label.lower() not in found_low:
                found.append(label)
                found_low.add(label.lower())

    return found[:limit]

def extract_jurisprudencia_refs(text: str, limit=12):
    patterns = [
        r"\bREsp\s*\d[\d\.\-\/]*\b",
        r"\bAgRg\b|\bAgInt\b|\bEDcl\b|\bEmbargos?\b",
        r"\bHC\s*\d[\d\.\-\/]*\b",
        r"\bRHC\s*\d[\d\.\-\/]*\b",
        r"\bADI\s*\d[\d\.\-\/]*\b|\bADPF\s*\d[\d\.\-\/]*\b|\bADC\s*\d[\d\.\-\/]*\b",
        r"\bTema\s*\d+\b",
        r"\bS[úu]mula\s*\d+\b"
    ]
    found = []
    found_low = set()
    for pat in patterns:
        for m in re.finditer(pat, text or "", flags=re.I):
            s = re.sub(r"\s+", " ", m.group(0).strip())
            k = s.lower()
            if s and k not in found_low:
                found.append(s)
                found_low.add(k)
            if len(found) >= limit:
                return found
    return found

def analyze_quality(text: str):
    t = (text or "").strip()
    warnings = []
    if len(t) < 450:
        warnings.append("Texto muito curto: a estrutura tende a ficar genérica.")
    return " ".join(warnings).strip()

def pick_best_question(text: str, fallback_base: str) -> str:
    candidates = [s for s in split_sentences(text) if s.endswith("?") and 15 <= len(s) <= 240]
    if candidates:
        return candidates[0].strip()

    markers = ["discute-se", "controvérsia", "questão", "trata-se", "cuidam os autos", "pretende"]
    for s in split_sentences(text)[:30]:
        low = s.lower()
        if any(m in low for m in markers) and 30 <= len(s) <= 260:
            if not s.endswith("?"):
                return f"{s.rstrip('.')}?"
            return s.strip()

    kws = pick_keywords(fallback_base, k=4)
    if kws:
        return f"Qual é o entendimento do tribunal sobre {', '.join(kws)}?"
    return "Qual é o entendimento do tribunal sobre o tema do caso?"

def suggest_library_links(text: str, max_items: int = 7):
    t = (text or "").lower()
    patterns = [
        (r"\bcf\b|\bcf/88\b|constitui", ["CF_HTML"]),
        (r"\bcpc\b|processo civil|art\.\s*1\.?0?13|art\.\s*927", ["CPC"]),
        (r"\bcpp\b|processo penal|habeas corpus", ["CPP"]),
        (r"\bcp\b|c[oó]digo penal|dolo|culpa|tipicidade", ["CP"]),
        (r"\bclt\b|trabalh|reclama[cç][aã]o trabalhista", ["CLT"]),
        (r"\bcdc\b|consumidor|fornecedor|rela[cç][aã]o de consumo", ["CDC"]),
        (r"\bctn\b|tribut[aá]rio|lan[cç]amento|credito tribut", ["CTN"]),
        (r"\beca\b|crian[cç]a|adolesc", ["ECA"]),
        (r"maria da penha|viol[eê]ncia dom[eé]stica", ["MPENHA"]),
        (r"\bidoso\b|estatuto do idoso", ["IDOSO"]),
        (r"improbidade|l\.?\s*8429|lei\s*8\.?429", ["LIA"]),
        (r"processo administrativo|l\.?\s*9784|lei\s*9\.?784", ["LPA"]),
        (r"licita[cç][aã]o|lei\s*14\.?133|l\.?\s*14133", ["LIC"]),
        (r"previd[eê]ncia|benef[ií]cio|aposentadoria|lei\s*8\.?213|l\.?\s*8213", ["LBPS"]),
        (r"direitos humanos|pacto de san jos[eé]|decreto\s*678|d\.?\s*678", ["PSSJ"]),
        (r"elei[cç][aã]o|tse|c[oó]digo eleitoral", ["CE", "LEI_ELEICOES"]),
    ]

    keys = []
    for pat, ks in patterns:
        if re.search(pat, t, flags=re.I):
            for k in ks:
                if k not in keys:
                    keys.append(k)

    if "GLOSS_STF" not in keys:
        keys.append("GLOSS_STF")

    for k in ["PORTAL_PLANALTO", "LIVROS_ABERTOS", "OAB"]:
        if k not in keys:
            keys.append(k)

    by_key = {i["key"]: i for i in LIBRARY_LINKS}
    out = []
    for k in keys:
        item = by_key.get(k)
        if item:
            out.append(item)
        if len(out) >= max_items:
            break
    return out

def build_search_queries(pergunta: str, tese: str, keywords: list[str], max_items: int = 4) -> list[str]:
    low = f"{pergunta} {tese}".lower()

    anchors = []
    for a in [
        "habeas corpus", "prisão preventiva", "excesso de prazo", "fundamentação",
        "contemporaneidade", "medidas cautelares", "tutela de urgência",
        "dano moral", "responsabilidade civil", "ônus da prova", "cerceamento"
    ]:
        if a in low:
            anchors.append(a)

    kws = [k for k in (keywords or []) if k and len(k) >= 4][:6]

    out = []
    def add(s: str):
        s = (s or "").strip()
        if not s:
            return
        if s.lower() in [x.lower() for x in out]:
            return
        out.append(s)

    if anchors:
        parts = [f"\"{anchors[0]}\""]
        for k in kws[:2]:
            parts.append(f"\"{k}\"")
        add(" AND ".join(parts))

    if kws:
        add(" AND ".join([f"\"{k}\"" for k in kws[:3]]))

    if len(anchors) >= 2:
        add(" OR ".join([f"\"{a}\"" for a in anchors[:3]]))

    if not out and anchors:
        add(f"\"{anchors[0]}\"")

    return out[:max_items]

def improve_user_question(raw: str, keywords: list[str]) -> dict:
    kws = [k for k in (keywords or []) if k][:4]
    tema = ", ".join(kws) if kws else "o tema do caso"

    pergunta_objetiva = f"Qual é o entendimento do tribunal sobre {tema}?"
    variantes = [
        f"Quais requisitos o tribunal exige em casos de {tema}?",
        f"Em quais hipóteses o tribunal afasta {tema}?",
        f"Quais fundamentos costumam ser determinantes em decisões sobre {tema}?",
    ]
    return {"pergunta_objetiva": pergunta_objetiva, "variantes": variantes}

def build_action_checklist(text: str) -> list[str]:
    low = (text or "").lower()
    items = []

    if "prisão preventiva" in low or "habeas" in low or "hc" in low:
        items.extend([
            "Identifique a medida: é HC? Revogação/substituição de preventiva? Relaxamento?",
            "Fundamento (CPP): há fatos concretos ou fundamentação genérica?",
            "Contemporaneidade: a decisão explica por que a cautelar é necessária agora?",
            "Excesso de prazo: marco inicial, prazo e justificativa do juízo/tribunal.",
            "Medidas diversas (art. 319 do CPP): analisou e justificou por que não aplicou?",
            "Dispositivo: concedeu/negou/parcial? Em quais termos?"
        ])

    if "tutela" in low or "urgência" in low:
        items.extend([
            "Tipo de tutela: urgência ou evidência? O pedido está claro?",
            "Probabilidade do direito: quais fatos/provas sustentam?",
            "Perigo de dano: qual risco imediato foi demonstrado?",
            "Reversibilidade: há risco de irreversibilidade?",
            "Dispositivo: deferiu/indeferiu? Com quais limites?"
        ])

    if "dano moral" in low or "responsabilidade" in low:
        items.extend([
            "Fato gerador: está descrito com clareza?",
            "Nexo causal: o texto conecta conduta e dano?",
            "Excludentes: culpa exclusiva, fortuito/força maior, exercício regular?",
            "Quantum: critérios e precedentes comparáveis.",
            "Dispositivo: condenou/absolveu? Valores/obrigações?"
        ])

    if not items:
        items = [
            "Qual é a controvérsia central (pergunta jurídica)?",
            "Quais fatos o julgador tratou como relevantes?",
            "Qual norma foi determinante (não só citada)?",
            "Qual precedente/tema/súmula foi aplicado (ou afastado) e por quê?",
            "Qual foi o resultado (dispositivo) e seus limites?"
        ]

    out = []
    seen = set()
    for i in items:
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out[:10]

def build_main_theme(keywords: list[str], fundamentos_normas: list[str], fundamentos_juris: list[str]) -> str:
    kws = [k for k in (keywords or []) if k][:4]
    norm_hint = ""
    for item in (fundamentos_normas or []):
        if "CPP" in item:
            norm_hint = " (processo penal)"
            break
        if "CPC" in item:
            norm_hint = " (processo civil)"
            break
        if "CP" in item and "CPP" not in item:
            norm_hint = " (direito penal)"
            break

    if kws:
        return f"{', '.join(kws)}{norm_hint}".strip()
    if fundamentos_juris and fundamentos_juris[0] and fundamentos_juris[0] != "(não identificado automaticamente)":
        return f"Tema relacionado a {fundamentos_juris[0]}{norm_hint}".strip()
    return "Tema não identificado automaticamente."

# =========================
# Núcleo da análise
# =========================
def build_output(text: str):
    text = normalize(text)

    ementa = extract_block(
        text,
        start_patterns=[r"\bementa\b"],
        stop_patterns=[r"\bac[oó]rd[aã]o\b", r"\brelat[oó]rio\b", r"\bvoto\b"],
        max_chars=1700
    ) or text[:900].strip()

    base_for_keywords = ementa or text[:1200]
    keywords = pick_keywords(base_for_keywords, k=8)

    pergunta = pick_best_question(text, base_for_keywords)

    tese = extract_block(
        text,
        start_patterns=[r"\btese\b", r"\bconclus[aã]o\b", r"\bdecide-se\b", r"\bdispositivo\b", r"\bante o exposto\b", r"\bisto posto\b"],
        stop_patterns=[r"\bfundamenta[cç][aã]o\b", r"\brelat[oó]rio\b"],
        max_chars=1600
    )
    if not tese:
        src = ementa or text[:900]
        sents = split_sentences(src)
        tese = " ".join(sents[:3]) if sents else (src[:400] if src else "")

    fundamentos_normas = extract_legal_citations(text, limit=14) or ["(não identificado automaticamente)"]
    fundamentos_juris = extract_jurisprudencia_refs(text, limit=12) or ["(não identificado automaticamente)"]

    resumo = " ".join(split_sentences(ementa)[:6]).strip()

    pesquisas = build_search_queries(pergunta, tese, keywords, max_items=4)
    improved_q = improve_user_question(request.form.get("texto", "") if request else "", keywords)

    alerta = analyze_quality(text)
    checklist = build_action_checklist(text)
    sugestoes = suggest_library_links(text, max_items=7)

    tema_principal = build_main_theme(keywords, fundamentos_normas, fundamentos_juris)
    termos_importantes = extract_terms_translation(text, max_items=10)

    return {
        "tema_principal": tema_principal,
        "pergunta": (pergunta or "").strip(),
        "pergunta_objetiva": improved_q.get("pergunta_objetiva", ""),
        "fundamentos_normas": fundamentos_normas,
        "fundamentos_juris": fundamentos_juris,
        "keywords": keywords[:8],
        "queries_juris": pesquisas,
        "checklist": checklist,
        "resumo": resumo,
        "alerta": alerta,
        "sugestoes": sugestoes,
        "termos_importantes": termos_importantes,
        "glossario_source": GLOSSARY_URL,
    }

# =========================
# Upload helpers
# =========================
def allowed_file(filename):
    return os.path.splitext((filename or "").lower())[1] in ALLOWED_EXTS

def extract_text_from_pdf(path):
    reader = PdfReader(path)
    chunks = []
    for p in reader.pages:
        txt = p.extract_text() or ""
        if txt.strip():
            chunks.append(txt)
    return "\n".join(chunks)

def extract_text_from_docx(path):
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def get_text_from_upload(file):
    filename = secure_filename(file.filename or "")
    if not filename:
        return ""

    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(
        UPLOAD_DIR,
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    )
    file.save(path)

    try:
        if ext == ".pdf":
            return extract_text_from_pdf(path)
        if ext == ".docx":
            return extract_text_from_docx(path)
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

# =========================
# ERROS (sem recursão)
# =========================
@app.errorhandler(TemplateNotFound)
def handle_template_not_found(e):
    return f"""
    <html><head><meta charset="utf-8"><title>Erro • Lumen Jurídico</title></head>
    <body style="font-family:Inter,Arial,sans-serif; background:#f4f6fa; margin:0;">
      <div style="max-width:860px; margin:0 auto; padding:24px;">
        <h1>Erro de template</h1>
        <p>O servidor não encontrou um arquivo de template.</p>
        <pre style="background:#fff;border:1px solid #d8dee9;padding:12px;border-radius:10px;white-space:pre-wrap;">{str(e)}</pre>
        <p style="margin-top:12px;"><a href="/" style="color:#1e3a8a;text-decoration:none;">Tentar voltar</a></p>
      </div>
    </body></html>
    """, 500

@app.errorhandler(500)
def handle_500(e):
    return """
    <html><head><meta charset="utf-8"><title>Erro • Lumen Jurídico</title></head>
    <body style="font-family:Inter,Arial,sans-serif; background:#f4f6fa; margin:0;">
      <div style="max-width:860px; margin:0 auto; padding:24px;">
        <h1>Erro interno</h1>
        <p>Ocorreu um erro ao processar sua solicitação.</p>
        <p style="color:#6b7280;">Abra os logs do Render para ver o erro completo.</p>
        <p style="margin-top:12px;"><a href="/" style="color:#1e3a8a;text-decoration:none;">Tentar voltar</a></p>
      </div>
    </body></html>
    """, 500

# =========================
# Rotas
# =========================
@app.get("/")
def home():
    return render_template("index.html")

@app.post("/analisar")
def analisar():
    texto = (request.form.get("texto") or "").strip()
    arquivo = request.files.get("arquivo")

    if arquivo and arquivo.filename:
        if not allowed_file(arquivo.filename):
            flash("Envie apenas PDF ou DOCX.", "error")
            return redirect(url_for("home"))

        extraido = (get_text_from_upload(arquivo) or "").strip()
        if not extraido:
            flash(
                "Não foi possível extrair texto do arquivo. "
                "Se for PDF escaneado (imagem), exporte para PDF pesquisável ou cole o texto manualmente.",
                "error"
            )
            return redirect(url_for("home"))

        texto = f"{texto}\n\n{extraido}".strip() if texto else extraido

    if not texto.strip():
        flash("Cole um texto ou envie um arquivo para análise.", "error")
        return redirect(url_for("home"))

    out = build_output(texto)
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now())

@app.get("/biblioteca")
def biblioteca():
    try:
        return render_template("biblioteca.html", links=LIBRARY_LINKS)
    except TemplateNotFound:
        items = "".join([
            f'<li style="margin:10px 0;">'
            f'<a href="{i["url"]}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#1e3a8a; text-decoration:none; font-weight:600;">{i["titulo"]}</a>'
            f' <span style="font-size:12px; padding:4px 8px; border:1px solid #d8dee9; '
            f'border-radius:999px; background:#f9fafb; margin-left:8px;">{i["tipo"]}</span>'
            f'</li>'
            for i in LIBRARY_LINKS
        ])
        return f"""
        <html><head><meta charset="utf-8"><title>Biblioteca • Lumen Jurídico</title></head>
        <body style="font-family:Inter,Arial,sans-serif; background:#f4f6fa; margin:0;">
          <div style="max-width:1020px; margin:0 auto; padding:24px;">
            <h1 style="margin:0 0 10px 0;">Biblioteca</h1>
            <p style="color:#6b7280; margin:0 0 16px 0;">Links oficiais e materiais úteis para consulta.</p>
            <div style="background:#fff; border:1px solid #d8dee9; border-radius:14px; padding:20px;">
              <ul style="margin:0; padding-left:18px;">{items}</ul>
            </div>
            <p style="margin-top:14px;">
              <a href="/" style="color:#1e3a8a; text-decoration:none;">← Voltar</a>
            </p>
          </div>
        </body></html>
        """

@app.get("/glossario")
def glossario():
    return redirect(GLOSSARY_URL)

@app.get("/sobre")
def sobre():
    return render_template("sobre.html")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
