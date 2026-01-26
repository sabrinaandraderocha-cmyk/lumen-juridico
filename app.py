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
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
ALLOWED_EXTS = {".pdf", ".docx", ".txt"}

# =========================
# Modelo do Banco de Dados
# =========================
class Analise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    titulo_resumo = db.Column(db.String(255))  # Ex: "Habeas Corpus - Tráfico"
    texto_original = db.Column(db.Text)        # O texto completo extraído
    tipo_peca = db.Column(db.String(100))      # Ex: Sentença
    
    def __repr__(self):
        return f'<Analise {self.id}>'

# Cria as tabelas ao iniciar
with app.app_context():
    db.create_all()

# =========================
# Glossário e Links (Mantidos do Original)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    {"key": "CF_PDF", "titulo": "Constituição Federal (PDF – DOU)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/DOUconstituicao88.pdf", "tipo": "Constituição"},
    {"key": "CF_HTML", "titulo": "Constituição Federal (texto compilado)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Código • Trabalho"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor (CDC)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Código • Consumidor"},
    {"key": "CTN", "titulo": "Código Tributário Nacional (CTN)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Código • Tributário"},
    {"key": "LIC", "titulo": "Lei de Licitações (Lei 14.133/2021)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/L14133.htm", "tipo": "Lei Administrativa"},
    {"key": "LIA", "titulo": "Lei de Improbidade (Lei 8.429/1992)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8429.htm", "tipo": "Lei Administrativa"},
    {"key": "ECA", "titulo": "Estatuto da Criança e Adolescente (ECA)", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm", "tipo": "Estatuto"},
    {"key": "MPENHA", "titulo": "Lei Maria da Penha (Lei 11.340/2006)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm", "tipo": "Lei Especial"},
    {"key": "STF_GLOSS", "titulo": "Glossário STF", "url": GLOSSARY_URL, "tipo": "Glossário"},
]

STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como","mais",
    "menos","já","não","sim","ser","foi","é","são","era","sendo","ter","tem","têm","haver","há",
    "art","artigo","lei","decreto","resolução","acórdão","relator","relatora","turma","câmara",
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso",
    "ementa","embargos","embargo","autos","vistos","juiz","juiza","excelencia"
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
}

# =========================
# Funções de Texto (NLP e Regex Originais)
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

def extract_legal_citations(text: str, limit=14):
    t = text or ""
    patterns = [
        r"Lei\s+n[º°]?\s*[\d\.]+(?:/\d{2,4})?",
        r"art\.\s*\d+[º°]?",
        r"Súmula\s*(?:Vinculante)?\s*n[º°]?\s*\d+",
        r"Constituição\s+Federal",
        r"Código\s+(?:Civil|Penal|Processo\s+Civil|Processo\s+Penal|Defesa\s+do\s+Consumidor|Tributário)",
        r"CF/88", r"CPC", r"CPP", r"CP", r"CLT", r"CDC", r"CTN"
    ]
    citations = []
    for pat in patterns:
        citations.extend(re.findall(pat, t, flags=re.IGNORECASE))
    
    seen = set()
    unique = []
    for c in citations:
        clean = normalize(c)
        if clean.lower() not in seen:
            seen.add(clean.lower())
            unique.append(clean)
    return unique[:limit]

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
    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, text or "", flags=re.I):
            s = re.sub(r"\s+", " ", m.group(0).strip())
            k = s.lower()
            if s and k not in seen:
                found.append(s)
                seen.add(k)
            if len(found) >= limit: return found
    return found

def analyze_quality(text: str):
    t = (text or "").strip()
    warnings = []
    if len(t) < 450:
        warnings.append("Texto muito curto: a análise pode ficar genérica.")
    return " ".join(warnings).strip()

def pick_best_question(text: str, fallback_base: str) -> str:
    candidates = [s for s in split_sentences(text) if s.endswith("?") and 15 <= len(s) <= 240]
    if candidates: return candidates[0].strip()
    return "Qual é a controvérsia jurídica principal deste caso?"

def extract_terms_translation(text: str, max_items: int = 10) -> list[dict]:
    t = (text or "").lower()
    hits = []
    seen = set()
    for term, tr in TERM_TRANSLATIONS.items():
        if term in t and term not in seen:
            seen.add(term)
            hits.append({"termo": term, "traducao": tr})
            if len(hits) >= max_items: break
    return hits

def build_search_queries(pergunta: str, tese: str, keywords: list[str], max_items: int = 4) -> list[str]:
    kws = [k for k in (keywords or []) if k and len(k) >= 4][:3]
    out = []
    if kws: out.append(" AND ".join([f'"{k}"' for k in kws]))
    if tese: out.append(tese[:100])
    return out[:max_items]

def build_action_checklist(text: str) -> list[str]:
    low = (text or "").lower()
    items = []
    if "prisão" in low or "hc" in low:
        items.extend(["Verificar fundamentação concreta", "Checar contemporaneidade", "Analisar excesso de prazo"])
    if "dano moral" in low:
        items.extend(["Verificar nexo causal", "Analisar critérios do quantum", "Checar excludentes"])
    if not items:
        items = ["Identificar controvérsia", "Listar fatos relevantes", "Verificar dispositivo", "Checar prazos"]
    return items

def suggest_library_links(text: str, max_items: int = 7):
    t = (text or "").lower()
    out = []
    for link in LIBRARY_LINKS:
        # Se o titulo ou a chave aparecer no texto, sugere (lógica simples)
        keywords = link['titulo'].split()
        if any(k.lower() in t for k in keywords if len(k) > 3):
            out.append(link)
    
    # Se não achou nada específico, manda os gerais
    if not out:
        out = [l for l in LIBRARY_LINKS if l['key'] in ['CF_HTML', 'CPC', 'STF_GLOSS']]
    
    return out[:max_items]

# =========================
# Lógica de Construção do Resultado
# =========================
def build_output(text: str):
    text = normalize(text)

    # Tenta extrair a ementa
    ementa = extract_block(text, [r"\bementa\b"], [r"\bac[oó]rd[aã]o\b", r"\brelat[oó]rio\b"], 1700) or text[:900]
    
    # Identifica Palavras-chave
    base_for_keywords = ementa or text[:1500]
    keywords = pick_keywords(base_for_keywords, k=8)

    # Identifica Pergunta e Tese
    pergunta = pick_best_question(text, base_for_keywords)
    tese = extract_block(text, [r"\btese\b", r"\bconclus[aã]o\b", r"\bdispositivo\b"], [r"\bfundamenta[cç][aã]o\b"], 1600)
    if not tese: tese = ementa[:500]

    # Extrações Jurídicas
    fundamentos_normas = extract_legal_citations(text, limit=14)
    fundamentos_juris = extract_jurisprudencia_refs(text, limit=12)
    
    # Resumo simples
    resumo = ementa[:600] + ("..." if len(ementa)>600 else "")

    # Auxiliares
    pesquisas = build_search_queries(pergunta, tese, keywords)
    checklist = build_action_checklist(text)
    sugestoes = suggest_library_links(text)
    termos_importantes = extract_terms_translation(text)
    
    # Define o tema principal (Título)
    tema_principal = f"Análise: {', '.join(keywords[:3])}" if keywords else "Análise Jurídica"

    return {
        "tema_principal": tema_principal.title(),
        "pergunta": pergunta,
        "fundamentos_normas": fundamentos_normas,
        "fundamentos_juris": fundamentos_juris,
        "keywords": keywords,
        "queries_juris": pesquisas,
        "checklist": checklist,
        "resumo": resumo,
        "alerta": analyze_quality(text),
        "sugestoes": sugestoes,
        "termos_importantes": termos_importantes,
        "glossario_source": GLOSSARY_URL,
    }

# =========================
# Upload Helpers
# =========================
def allowed_file(filename):
    return os.path.splitext((filename or "").lower())[1] in ALLOWED_EXTS

def get_text_from_upload(file):
    filename = secure_filename(file.filename or "")
    if not filename: return ""
    
    ext = os.path.splitext(filename)[1].lower()
    path = os.path.join(UPLOAD_DIR, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
    file.save(path)

    text = ""
    try:
        if ext == ".pdf":
            reader = PdfReader(path)
            text = "\n".join([p.extract_text() or "" for p in reader.pages])
        elif ext == ".docx":
            doc = Document(path)
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        elif ext == ".txt":
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
    except Exception as e:
        print(f"Erro ao ler arquivo: {e}")
    finally:
        try: os.remove(path)
        except: pass
    
    return text

# =========================
# Rotas
# =========================
@app.route("/")
def home():
    # Pega histórico do banco
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

    # Processa (Usa a lógica original + melhorias)
    out = build_output(texto)
    
    # Salva no Banco de Dados
    nova = Analise(
        titulo_resumo=out["tema_principal"],
        texto_original=texto,
        tipo_peca="Documento Jurídico" # Poderia melhorar com IA
    )
    db.session.add(nova)
    db.session.commit()

    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

@app.route("/resultado/<int:id>")
def resultado(id):
    analise = Analise.query.get_or_404(id)
    # Refaz o processamento para exibir (evita salvar JSON complexo no SQLite simples)
    out = build_output(analise.texto_original)
    return render_template("resultado.html", out=out, texto=analise.texto_original, now=datetime.now(), analise_id=analise.id)

@app.route("/historico")
def historico():
    # Paginação simples
    page = request.args.get('page', 1, type=int)
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

# Errors
@app.errorhandler(404)
def page_not_found(e): return render_template('404.html'), 404
@app.errorhandler(500)
def server_error(e): return render_template('500.html'), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
