import os
import re
import json
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from flask import (
    Flask, render_template, request, redirect, url_for, flash, jsonify
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

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
# (Scraping ficou instável no STF -> agora abrimos o link oficial direto)
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


def extract_legal_citations(text: str, limit=12):
    patterns = [
        r"\bart\.?\s*\d+[a-zA-Zº°]*\b(?:\s*,\s*§\s*\d+º?)?(?:\s*,\s*inc\.\s*[ivxlcdm]+)?",
        r"\blei\s*n[ºo]\s*\d[\d\.\-]*\s*(?:/|\s*de\s*)\s*\d{2,4}\b",
        r"\bdecreto-lei\s*n[ºo]\s*\d[\d\.\-]*\b",
        r"\bconstitui[cç][aã]o\s*federal\b|\bCF/88\b|\bCF\b",
        r"\bCPC\b|\bCPP\b|\bCP\b|\bCLT\b|\bCDC\b|\bCTN\b"
    ]
    found = []
    found_low = set()
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            snippet = re.sub(r"\s+", " ", m.group(0).strip())
            key = snippet.lower()
            if snippet and key not in found_low:
                found.append(snippet)
                found_low.add(key)
            if len(found) >= limit:
                return found
    return found


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
        for m in re.finditer(pat, text, flags=re.I):
            s = re.sub(r"\s+", " ", m.group(0).strip())
            k = s.lower()
            if s and k not in found_low:
                found.append(s)
                found_low.add(k)
            if len(found) >= limit:
                return found
    return found


def guess_rule_exception(tese: str):
    separators = ["ressalv", "exceto", "salvo", "contudo", "entretanto", "todavia", "porém", "no entanto"]
    low = (tese or "").lower()
    for sep in separators:
        idx = low.find(sep)
        if idx != -1 and idx > 20:
            regra = tese[:idx].strip(" .;:-")
            excecao = tese[idx:].strip(" .;:-")
            return regra, excecao
    return (tese or "").strip(), ""


def analyze_quality(text: str):
    t = (text or "").strip()
    warnings = []
    if len(t) < 450:
        warnings.append("Texto muito curto: a estrutura tende a ficar genérica.")
    has_numbers = bool(re.search(r"\d{2,}", t))
    has_parties = bool(re.search(r"\b(autor|réu|ré|impetrante|paciente|apelante|agravante|recorrente)\b", t, flags=re.I))
    has_request = bool(re.search(r"\b(pede|requer|postula|pleiteia|pretende|busca)\b", t, flags=re.I))
    if not (has_numbers or has_parties or has_request):
        warnings.append("Poucos elementos fáticos (partes/pedidos/números): a confiança da síntese cai.")
    return " ".join(warnings).strip()


def confidence_score(text: str) -> dict:
    t = (text or "")
    score = 0
    reasons = []

    if len(t.strip()) >= 1200:
        score += 25
    elif len(t.strip()) >= 600:
        score += 15
    else:
        score += 5
        reasons.append("Pouco texto para estruturar com precisão.")

    if re.search(r"\b(autor|réu|impetrante|paciente|apelante|agravante|recorrente)\b", t, flags=re.I):
        score += 15
    else:
        reasons.append("Sem indicação clara de partes/processualidade.")

    if re.search(r"\b(pede|requer|pleiteia|postula|pretende)\b", t, flags=re.I):
        score += 15
    else:
        reasons.append("Sem pedido explícito.")

    if re.search(r"\b(decide|defiro|indefiro|julgo|condeno|absolvo|nego provimento|dou provimento)\b", t, flags=re.I):
        score += 20
    else:
        reasons.append("Sem dispositivo/resultado claramente identificável.")

    if extract_legal_citations(t, limit=3):
        score += 10
    else:
        reasons.append("Pouca referência normativa identificável.")

    if extract_jurisprudencia_refs(t, limit=2):
        score += 10

    if score >= 70:
        level = "alta"
    elif score >= 45:
        level = "média"
    else:
        level = "baixa"

    return {"nivel": level, "score": min(score, 100), "motivos": reasons[:4]}


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
    """
    “Pesquisas prontas” curtas e úteis (evita colar frases enormes).
    """
    low = f"{pergunta} {tese}".lower()

    anchors = []
    for a in [
        "habeas corpus", "prisão preventiva", "excesso de prazo", "fundamentação",
        "contemporaneidade", "medidas cautelares", "art. 312", "art. 319",
        "tutela de urgência", "probabilidade do direito", "perigo de dano",
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
    """
    Pergunta objetiva SEM “X e Y”.
    """
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
            "A decisão fundamenta concretamente o periculum libertatis (ou é genérica)?",
            "Há contemporaneidade dos fundamentos da preventiva?",
            "Existe excesso de prazo? Há justificativas (complexidade, diligências, pluralidade de réus)?",
            "Foram consideradas medidas cautelares diversas (art. 319 do CPP), com motivação?"
        ])

    if "tutela" in low or "urgência" in low:
        items.extend([
            "Probabilidade do direito: quais fatos e documentos sustentam?",
            "Perigo de dano/risco ao resultado útil: qual prova do risco?",
            "Reversibilidade: há risco de irreversibilidade da medida?"
        ])

    if "dano moral" in low or "responsabilidade" in low:
        items.extend([
            "Conduta, dano e nexo: estão descritos e provados?",
            "Há excludentes (culpa exclusiva, caso fortuito/força maior)?",
            "Parâmetros de quantificação: há precedentes comparáveis?"
        ])

    if not items:
        items = [
            "Qual é o pedido e qual foi o resultado (deferiu/negou/proveu)?",
            "Quais fatos foram considerados relevantes?",
            "Qual norma foi determinante (não só citada)?",
            "Há precedente obrigatório (Tema/Súmula) aplicável ou distinguishing?"
        ]

    out = []
    seen = set()
    for i in items:
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out[:8]

# =========================
# Núcleo da análise
# =========================
def build_output(text: str):
    text = normalize(text)

    relatorio = extract_block(
        text,
        start_patterns=[r"\brelat[oó]rio\b", r"\bs[ií]ntese\b", r"\bcuidam os autos\b", r"\btrata-se\b"],
        stop_patterns=[r"\bfundamenta[cç][aã]o\b", r"\bm[eé]rito\b", r"\bvoto\b", r"\bdispositivo\b", r"\bdecido\b"],
        max_chars=2400
    )

    fundamentacao = extract_block(
        text,
        start_patterns=[r"\bfundamenta[cç][aã]o\b", r"\bm[eé]rito\b", r"\braz[oõ]es\b", r"\bconsidera[cç][aã]o\b"],
        stop_patterns=[r"\bdispositivo\b", r"\bdecido\b", r"\bisto posto\b", r"\bante o exposto\b"],
        max_chars=2600
    )

    dispositivo = extract_block(
        text,
        start_patterns=[r"\bdispositivo\b", r"\bdecido\b", r"\bisto posto\b", r"\bante o exposto\b"],
        stop_patterns=[r"\bpublique-se\b", r"\bintime-se\b", r"\btr[aâ]nsito\b"],
        max_chars=1400
    )

    ementa = extract_block(
        text,
        start_patterns=[r"\bementa\b"],
        stop_patterns=[r"\bac[oó]rd[aã]o\b", r"\brelat[oó]rio\b", r"\bvoto\b"],
        max_chars=1700
    ) or text[:900].strip()

    base_for_keywords = ementa or fundamentacao or relatorio or text[:1200]
    keywords = pick_keywords(base_for_keywords, k=8)

    pergunta = pick_best_question(text, base_for_keywords)

    tese = extract_block(
        text,
        start_patterns=[r"\btese\b", r"\bconclus[aã]o\b", r"\bdecide-se\b", r"\bdispositivo\b", r"\bante o exposto\b", r"\bisto posto\b"],
        stop_patterns=[r"\bfundamenta[cç][aã]o\b", r"\brelat[oó]rio\b"],
        max_chars=1600
    )
    if not tese:
        src = dispositivo or ementa or fundamentacao or text[:900]
        sents = split_sentences(src)
        tese = " ".join(sents[:3]) if sents else (src[:400] if src else "")

    tese_regra, tese_excecao = guess_rule_exception(tese)

    fundamentos_normas = extract_legal_citations(text, limit=12) or ["(não identificado automaticamente)"]
    fundamentos_juris = extract_jurisprudencia_refs(text, limit=12) or ["(não identificado automaticamente)"]

    resumo_src = relatorio or ementa or text[:1200]
    resumo = " ".join(split_sentences(resumo_src)[:6]).strip()

    controvertidos = []
    for s in split_sentences(fundamentacao or relatorio or text)[:40]:
        low = s.lower()
        if any(x in low for x in ["discute-se", "controvérsia", "questão", "debate", "alega", "sustenta", "argumenta", "impugna"]):
            controvertidos.append(s.rstrip(".").strip())
    if not controvertidos:
        if keywords:
            controvertidos = [f"Delimitação do tema: {', '.join(keywords[:4])}."]
        else:
            controvertidos = ["Delimitação do tema central e requisitos aplicáveis ao caso."]
    controvertidos = controvertidos[:6]

    pesquisas = build_search_queries(pergunta, tese, keywords, max_items=4)

    improved_q = improve_user_question(request.form.get("texto", "") if request else "", keywords)

    alerta = analyze_quality(text)
    conf = confidence_score(text)

    checklist = build_action_checklist(text)

    sugestoes = suggest_library_links(text, max_items=7)

    low = text.lower()
    hints = []
    if any(w in low for w in ["concurso", "prova objetiva", "questão", "exame da ordem", "oab"]):
        hints.append("Estudo/Prova: use as palavras-chave e as pesquisas prontas para achar casos semelhantes e padrões de fundamentação.")
    if any(w in low for w in ["petição", "inicial", "contestação", "recurso", "agravo", "apelação", "habeas corpus", "mandado de segurança"]):
        hints.append("Prática: transforme a tese em tópicos e valide com precedentes (Tema/Súmula/HC/REsp) antes de usar na peça.")
    if not hints:
        hints.append("Use como base para: (i) delimitar controvérsia; (ii) comparar casos; (iii) checar requisitos; (iv) montar pesquisa replicável.")

    return {
        "pergunta": (pergunta or "").strip(),
        "tese": (tese or "").strip(),
        "tese_regra": (tese_regra or "").strip(),
        "tese_excecao": (tese_excecao or "").strip(),
        "fundamentos_normas": fundamentos_normas,
        "fundamentos_juris": fundamentos_juris,
        "keywords": keywords[:8],
        "aplicacao": " ".join(hints).strip(),
        "ementa": (ementa or "").strip(),
        "alerta": alerta,
        "sugestoes": sugestoes,

        "resumo": resumo,
        "relatorio": relatorio.strip() if relatorio else "",
        "fundamentacao": fundamentacao.strip() if fundamentacao else "",
        "dispositivo": dispositivo.strip() if dispositivo else "",
        "pontos_controvertidos": controvertidos,

        "queries_juris": pesquisas,  # mantemos a chave para compatibilidade com o template
        "checklist": checklist,
        "confianca": conf,

        "pergunta_objetiva": improved_q.get("pergunta_objetiva", ""),
        "perguntas_variantes": improved_q.get("variantes", []),

        # agora o glossário é só link (não tentamos cache)
        "glossario_hits": [],
        "glossario_source": GLOSSARY_URL,
        "glossario_updated_at": None,
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
    return render_template("biblioteca.html", links=LIBRARY_LINKS)


@app.get("/glossario")
def glossario():
    return redirect(GLOSSARY_URL)


@app.get("/sobre")
def sobre():
    return render_template("sobre.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
