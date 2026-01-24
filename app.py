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
# Glossário STF (cache local)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"
GLOSSARY_CACHE_PATH = os.path.join(INSTANCE_DIR, "glossario_stf.json")
GLOSSARY_UPDATE_DAYS = int(os.getenv("GLOSSARY_UPDATE_DAYS", "7"))  # atualiza a cada 7 dias (default)


def _now_ts() -> int:
    return int(time.time())


def _strip_accents(s: str) -> str:
    s = s or ""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s)
        if unicodedata.category(ch) != "Mn"
    )


def normalize_term(s: str) -> str:
    s = (s or "").strip().lower()
    s = _strip_accents(s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s\-\/]", "", s)  # remove pontuações “soltas”
    return s.strip()


class STFGlossaryHTMLParser(HTMLParser):
    """
    Parser "tolerante": tenta extrair pares (termo, definicao) do HTML
    sem depender muito da estrutura exata da página.

    Estratégia:
    - Captura textos visíveis.
    - Faz uma varredura por padrões comuns (Termo: definição)
      e também heurística em blocos.
    """
    def __init__(self):
        super().__init__()
        self._texts = []
        self._skip = False
        self._tag_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._tag_stack.append(tag)
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        # desempilha até achar o tag
        while self._tag_stack:
            t = self._tag_stack.pop()
            if t == tag:
                break
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if self._skip:
            return
        txt = (data or "").strip()
        if not txt:
            return
        # evita lixo típico de navegação
        if len(txt) <= 1:
            return
        self._texts.append(txt)

    def get_text(self) -> str:
        # cola preservando “quebras lógicas”
        raw = "\n".join(self._texts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def load_glossary_cache() -> dict:
    if not os.path.exists(GLOSSARY_CACHE_PATH):
        return {}
    try:
        with open(GLOSSARY_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data:
            return data
        return {}
    except Exception:
        return {}


def save_glossary_cache(items: list[dict]):
    payload = {
        "source": GLOSSARY_URL,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    with open(GLOSSARY_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def cache_is_stale(cache: dict) -> bool:
    try:
        updated_at = cache.get("updated_at")
        if not updated_at:
            return True
        dt = datetime.fromisoformat(updated_at)
        return datetime.now() - dt > timedelta(days=GLOSSARY_UPDATE_DAYS)
    except Exception:
        return True


def fetch_glossary_html() -> str:
    # user-agent ajuda alguns servidores a devolverem HTML normal
    req = Request(GLOSSARY_URL, headers={"User-Agent": "Mozilla/5.0 (LumenJuridico/1.0)"})
    with urlopen(req, timeout=15) as resp:
        raw = resp.read()
    # tenta utf-8, fallback latin-1
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


def parse_glossary_items_from_html(html: str) -> list[dict]:
    """
    Saída: [{"term": "...", "definition": "...", "norm": "..."}]
    """
    parser = STFGlossaryHTMLParser()
    parser.feed(html or "")
    text = parser.get_text()

    # Heurística 1: linhas com "Termo - definição" ou "Termo: definição"
    items = []
    seen = set()

    # Normaliza quebras
    lines = [ln.strip() for ln in re.split(r"\n+", text) if ln.strip()]

    # Tenta achar blocos que pareçam entradas
    # Ex.: "Habeas corpus - Remédio constitucional ..."
    pattern = re.compile(r"^([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\s\-\(\)\/]{2,80})\s*[:\-–—]\s*(.{20,})$")

    for ln in lines:
        m = pattern.match(ln)
        if not m:
            continue
        term = m.group(1).strip()
        definition = m.group(2).strip()
        norm = normalize_term(term)
        if len(term) < 3 or len(definition) < 20:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        items.append({"term": term, "definition": definition, "norm": norm})

    # Heurística 2: se vier muito pouco, tenta pares em sequência (termo em linha curta + definição na próxima)
    if len(items) < 50:
        items2 = []
        seen2 = set(seen)
        for i in range(len(lines) - 1):
            a = lines[i]
            b = lines[i + 1]
            # termo costuma ser mais curto e “título”
            if 3 <= len(a) <= 80 and len(b) >= 30:
                # evita menus e títulos genéricos
                bad = ["glossário", "jurisprudência", "voltar", "portal", "stf", "pesquisa"]
                if any(x in a.lower() for x in bad):
                    continue
                # se a linha "a" parece uma definição (tem ponto demais), pula
                if a.count(".") >= 2:
                    continue
                term = a.strip(":-–— ").strip()
                definition = b.strip()
                norm = normalize_term(term)
                if norm and norm not in seen2:
                    seen2.add(norm)
                    items2.append({"term": term, "definition": definition, "norm": norm})
        items.extend(items2)

    # Filtra entradas muito ruins
    clean = []
    for it in items:
        t = (it.get("term") or "").strip()
        d = (it.get("definition") or "").strip()
        if not t or not d:
            continue
        if len(t) > 120:
            continue
        if len(d) < 25:
            continue
        clean.append(it)

    # Dedup final por norm
    out = []
    seen = set()
    for it in clean:
        n = it.get("norm") or normalize_term(it.get("term", ""))
        if not n or n in seen:
            continue
        seen.add(n)
        out.append({"term": it["term"], "definition": it["definition"], "norm": n})

    return out


def ensure_glossary_loaded(force_update: bool = False) -> dict:
    """
    Retorna cache carregado; se estiver stale e der, atualiza.
    Nunca quebra o app se a internet falhar.
    """
    cache = load_glossary_cache()

    if force_update or not cache or cache_is_stale(cache):
        try:
            html = fetch_glossary_html()
            items = parse_glossary_items_from_html(html)
            # só salva se parecer minimamente válido
            if len(items) >= 50:
                save_glossary_cache(items)
                cache = load_glossary_cache()
        except Exception:
            # mantém o cache antigo, se existir
            pass

    return cache or {"items": [], "updated_at": None, "source": GLOSSARY_URL}


def glossary_search(q: str, limit: int = 10) -> list[dict]:
    cache = ensure_glossary_loaded(force_update=False)
    items = cache.get("items") or []
    qn = normalize_term(q)

    if not qn:
        return []

    scored = []
    for it in items:
        term = it.get("term") or ""
        definition = it.get("definition") or ""
        norm = it.get("norm") or normalize_term(term)
        if not norm:
            continue

        # ranking simples
        score = 0
        if norm == qn:
            score += 100
        if norm.startswith(qn):
            score += 60
        if qn in norm:
            score += 35
        # também busca na definição, mas com peso menor
        if qn and qn in normalize_term(definition):
            score += 10

        if score > 0:
            scored.append((score, term, definition))

    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    out = []
    for score, term, definition in scored[:limit]:
        out.append({"term": term, "definition": definition})
    return out


def detect_glossary_terms_in_text(text: str, max_hits: int = 10) -> list[dict]:
    """
    Tenta encontrar termos do glossário citados no texto analisado.
    Heurística: procura por termos curtos/médios e expressões bem típicas.
    """
    cache = ensure_glossary_loaded(force_update=False)
    items = cache.get("items") or []
    if not items:
        return []

    t_norm = normalize_term(text)
    if not t_norm:
        return []

    # Seleciona candidatos do glossário por "palavras-chave do texto"
    tokens = set(re.findall(r"[a-z0-9]{4,}", t_norm))
    if not tokens:
        return []

    candidates = []
    for it in items:
        term = it.get("term") or ""
        norm = it.get("norm") or normalize_term(term)
        if not norm:
            continue
        # evita termos gigantes (tendem a dar falso positivo)
        if len(norm) > 45:
            continue

        # se termo é composto, checa primeiro e último token
        parts = norm.split()
        if parts:
            if parts[0] in tokens or parts[-1] in tokens:
                # checa ocorrência literal do termo no texto normalizado
                if f" {norm} " in f" {t_norm} ":
                    candidates.append(it)

    # Ordena por termos mais longos primeiro (mais específicos) e corta
    candidates.sort(key=lambda x: len(x.get("norm", "")), reverse=True)

    hits = []
    seen = set()
    for it in candidates:
        n = it.get("norm")
        if not n or n in seen:
            continue
        seen.add(n)
        hits.append({"term": it.get("term", ""), "definition": it.get("definition", "")})
        if len(hits) >= max_hits:
            break
    return hits


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

    # Glossário STF (atalho)
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
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso"
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
    # “fatos” mínimos: datas, nomes, valores, número do processo etc.
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
            # transforma em pergunta, quando fizer sentido
            if not s.endswith("?"):
                return f"{s.rstrip('.')}?"
            return s.strip()

    kws = pick_keywords(fallback_base, k=3)
    if kws:
        return f"É possível X em Y no contexto de {', '.join(kws)}?"
    return "É possível X em Y no contexto do caso?"


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

    # Glossário STF ajuda bastante pra termos técnicos em decisões
    if "GLOSS_STF" not in keys:
        keys.append("GLOSS_STF")

    # fallback: sempre úteis
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


def build_search_queries(pergunta: str, tese: str, keywords: list[str], max_items: int = 5) -> list[str]:
    """
    Gera consultas prontas para copiar/colar em pesquisa de jurisprudência.
    """
    q = []
    base_terms = [w for w in (keywords or [])[:6] if w]
    # “âncoras” comuns
    anchors = []
    low = f"{pergunta} {tese}".lower()
    for a in ["habeas corpus", "prisão preventiva", "excesso de prazo", "fundamentação", "contemporaneidade",
              "nulidade", "cerceamento", "recurso", "dano moral", "responsabilidade civil", "tutela de urgência"]:
        if a in low:
            anchors.append(a)

    # consulta 1: termo principal + palavras-chave
    terms = []
    if anchors:
        terms.append(f"\"{anchors[0]}\"")
    if base_terms:
        terms.extend([f"\"{t}\"" for t in base_terms[:4]])
    if terms:
        q.append(" AND ".join(terms))

    # consulta 2: pergunta em forma curta (sem pontuação)
    p = re.sub(r"[^\w\sÀ-ÿ]", "", (pergunta or "")).strip()
    p = re.sub(r"\s+", " ", p)
    if len(p) >= 25:
        q.append(p[:140])

    # consulta 3: tese regra + termos
    tr = re.sub(r"\s+", " ", (tese or "")).strip()
    if len(tr) >= 40:
        q.append(tr[:160])

    # variações úteis (OR)
    if anchors:
        alt = [f"\"{a}\"" for a in anchors[:3]]
        if alt:
            q.append(" OR ".join(alt))

    # limpa e corta
    out = []
    seen = set()
    for s in q:
        s2 = s.strip()
        if not s2:
            continue
        k = s2.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s2)
        if len(out) >= max_items:
            break
    return out


def improve_user_question(raw: str, keywords: list[str]) -> dict:
    """
    Converte texto cru / desabafo em perguntas jurídicas objetivas.
    Retorna:
      - pergunta_objetiva
      - variantes (lista)
    """
    raw = (raw or "").strip()
    kws = [k for k in (keywords or []) if k]
    base = "o caso"

    # tenta inferir área/medida
    low = raw.lower()
    if "habeas" in low or "pris" in low or "preventiva" in low:
        base = "prisão preventiva / habeas corpus"
    elif "consum" in low or "fornecedor" in low:
        base = "relação de consumo"
    elif "trabalh" in low or "clt" in low:
        base = "relação de trabalho"
    elif "tutela" in low or "urgência" in low:
        base = "tutela provisória"

    core = kws[:3]
    if core:
        tema = ", ".join(core)
    else:
        tema = base

    pergunta_objetiva = f"É possível X em Y considerando {tema}?"
    variantes = [
        f"Quais são os requisitos para X no contexto de {tema}?",
        f"Em quais hipóteses o tribunal admite/nega X quando há {tema}?",
        f"Há nulidade ou ilegalidade em Y quando se verifica {tema}?",
    ]

    # se houver muito discurso opinativo, tenta “trazer para o jurídico”
    if len(raw) > 0 and not raw.endswith("?"):
        variantes.insert(0, f"Qual é a controvérsia jurídica central envolvendo {tema}?")

    return {"pergunta_objetiva": pergunta_objetiva, "variantes": variantes[:4]}


def build_action_checklist(text: str) -> list[str]:
    """
    Checklist “do que checar” (útil pra peça/estudo).
    """
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
    # dedup preservando ordem
    out = []
    seen = set()
    for i in items:
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out[:8]


# =========================
# Núcleo da análise (melhorado)
# =========================
def build_output(text: str):
    text = normalize(text)

    # tenta estruturar em blocos clássicos
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
    )

    base_for_keywords = ementa or fundamentacao or relatorio or text[:1200]
    keywords = pick_keywords(base_for_keywords, k=8)

    # pergunta + tese “úteis”
    pergunta = pick_best_question(text, base_for_keywords)

    tese = extract_block(
        text,
        start_patterns=[r"\btese\b", r"\bconclus[aã]o\b", r"\bdecide-se\b", r"\bdispositivo\b", r"\bante o exposto\b", r"\bisto posto\b"],
        stop_patterns=[r"\bfundamenta[cç][aã]o\b", r"\brelat[oó]rio\b"],
        max_chars=1600
    )
    if not tese:
        # fallback: 2-3 frases do dispositivo ou ementa
        src = dispositivo or ementa or fundamentacao or text[:900]
        sents = split_sentences(src)
        tese = " ".join(sents[:3]) if sents else (src[:400] if src else "")

    tese_regra, tese_excecao = guess_rule_exception(tese)

    fundamentos_normas = extract_legal_citations(text, limit=12) or ["(não identificado automaticamente)"]
    fundamentos_juris = extract_jurisprudencia_refs(text, limit=12) or ["(não identificado automaticamente)"]

    # resumo curto (5–8 linhas)
    resumo_src = relatorio or ementa or text[:1200]
    resumo_sents = split_sentences(resumo_src)[:6]
    resumo = " ".join(resumo_sents).strip()

    # pontos controvertidos (heurística simples)
    controvertidos = []
    for s in split_sentences(fundamentacao or relatorio or text)[:40]:
        low = s.lower()
        if any(x in low for x in ["discute-se", "controvérsia", "questão", "debate", "alega", "sustenta", "argumenta", "impugna"]):
            controvertidos.append(s.rstrip(".").strip())
    if not controvertidos:
        # gera com base em keywords
        if keywords:
            controvertidos = [f"Delimitação do tema: {', '.join(keywords[:4])}."]
        else:
            controvertidos = ["Delimitação do tema central e requisitos aplicáveis ao caso."]

    controvertidos = controvertidos[:6]

    # queries prontas de pesquisa
    queries = build_search_queries(pergunta, tese, keywords, max_items=5)

    # “modo desabafo” -> pergunta objetiva + variantes
    improved_q = improve_user_question(request.form.get("texto", "") if request else "", keywords)

    # alertas + confiança
    alerta = analyze_quality(text)
    conf = confidence_score(text)

    # checklist prático
    checklist = build_action_checklist(text)

    # glossário: tenta hits automáticos
    glossary_hits = detect_glossary_terms_in_text(f"{tese}\n{fundamentos_normas}\n{fundamentos_juris}\n{text}", max_hits=10)

    # sugestões de biblioteca (com glossário incluso)
    sugestoes = suggest_library_links(text, max_items=7)

    # aplicação (mantém sua ideia, mas mais acionável)
    low = text.lower()
    hints = []
    if any(w in low for w in ["concurso", "prova objetiva", "questão", "exame da ordem", "oab"]):
        hints.append("Estudo/Prova: use as queries prontas + palavras-chave para achar casos semelhantes e padrões de fundamentação.")
    if any(w in low for w in ["petição", "inicial", "contestação", "recurso", "agravo", "apelação", "habeas corpus", "mandado de segurança"]):
        hints.append("Prática: transforme a tese em tópicos de fundamentação e valide com precedentes (Tema/Súmula/HC/REsp) antes de usar na peça.")
    if not hints:
        hints.append("Use como base para: (i) delimitar controvérsia; (ii) comparar casos; (iii) checar requisitos; (iv) montar uma pesquisa de jurisprudência replicável.")

    return {
        # compatibilidade com seu template atual
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

        # NOVOS: análise mais útil
        "resumo": resumo,
        "relatorio": relatorio.strip() if relatorio else "",
        "fundamentacao": fundamentacao.strip() if fundamentacao else "",
        "dispositivo": dispositivo.strip() if dispositivo else "",
        "pontos_controvertidos": controvertidos,
        "queries_juris": queries,
        "checklist": checklist,
        "confianca": conf,
        "pergunta_objetiva": improved_q.get("pergunta_objetiva", ""),
        "perguntas_variantes": improved_q.get("variantes", []),

        # NOVO: glossário
        "glossario_hits": glossary_hits,
        "glossario_source": GLOSSARY_URL,
        "glossario_updated_at": (ensure_glossary_loaded().get("updated_at")),
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

    # garante glossário carregado (sem travar se falhar)
    ensure_glossary_loaded(force_update=False)

    out = build_output(texto)
    return render_template("resultado.html", out=out, texto=texto, now=datetime.now())


@app.get("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)


@app.get("/glossario")
def glossario():
    """
    Tela do glossário. Se você criar um template 'glossario.html', ótimo.
    Se não existir, entrega uma página simples com busca.
    """
    cache = ensure_glossary_loaded(force_update=False)
    updated_at = cache.get("updated_at")
    total = len(cache.get("items") or [])

    try:
        return render_template(
            "glossario.html",
            source=GLOSSARY_URL,
            updated_at=updated_at,
            total=total
        )
    except TemplateNotFound:
        # fallback simples para não quebrar em deploy
        return f"""
        <html><head><meta charset="utf-8"><title>Glossário STF — Lumen</title></head>
        <body style="font-family: Arial, sans-serif; padding: 16px;">
          <h1>Glossário STF</h1>
          <p>Fonte: <a href="{GLOSSARY_URL}" target="_blank" rel="noopener">STF</a></p>
          <p>Atualizado em: {updated_at or "—"} • Itens em cache: {total}</p>
          <hr/>
          <h3>Buscar termo</h3>
          <form method="get" action="/api/glossario" onsubmit="event.preventDefault(); doSearch();">
            <input id="q" placeholder="Ex.: habeas corpus" style="padding:8px; width:320px;" />
            <button style="padding:8px;">Buscar</button>
          </form>
          <pre id="out" style="white-space: pre-wrap; margin-top: 12px;"></pre>
          <script>
            async function doSearch(){{
              const q = document.getElementById('q').value;
              const res = await fetch('/api/glossario?q=' + encodeURIComponent(q));
              const data = await res.json();
              document.getElementById('out').textContent = JSON.stringify(data, null, 2);
            }}
          </script>
        </body></html>
        """


@app.get("/api/glossario")
def api_glossario():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"q": q, "items": []})
    items = glossary_search(q, limit=12)
    return jsonify({"q": q, "items": items, "source": GLOSSARY_URL})


@app.post("/admin/glossario/atualizar")
def admin_glossario_atualizar():
    """
    Endpoint simples para forçar atualização do cache (se você quiser chamar via botão).
    Segurança: se você tiver login/admin, proteja esta rota.
    """
    ensure_glossary_loaded(force_update=True)
    flash("Glossário: tentativa de atualização realizada (se a internet estiver disponível).", "success")
    return redirect(url_for("glossario"))


@app.get("/sobre")
def sobre():
    return render_template("sobre.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    # Em produção (Render), debug=False mesmo.
    app.run(host="0.0.0.0", port=port, debug=False)
