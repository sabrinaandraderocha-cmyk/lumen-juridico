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
    titulo_resumo = db.Column(db.String(255))
    texto_original = db.Column(db.Text)
    tipo_peca = db.Column(db.String(100))
    
    def __repr__(self):
        return f'<Analise {self.id}>'

with app.app_context():
    db.create_all()

# =========================
# Dados Estáticos (Links e Stopwords)
# =========================
GLOSSARY_URL = "https://portal.stf.jus.br/jurisprudencia/glossario.asp"

LIBRARY_LINKS = [
    {"key": "CF_HTML", "titulo": "Constituição Federal (Compilado)", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},
    {"key": "CURSO_STF", "titulo": "Cursos EAD – Supremo Tribunal Federal", "url": "https://ead.stf.jus.br/course/index.php?categoryid=3", "tipo": "🎓 Curso Gratuito"},
    {"key": "CURSO_ESA", "titulo": "ESA OAB – Cursos Gratuitos", "url": "https://esa.oab.org.br/home/ver-cursos?filter_categories_id%5B%5D=24", "tipo": "🎓 Curso Gratuito"},
]

STOPWORDS_PT = {
    "a","o","os","as","um","uma","uns","umas","de","do","da","dos","das","em","no","na","nos","nas",
    "por","para","com","sem","sobre","entre","e","ou","que","se","ao","aos","à","às","como","mais",
    "menos","já","não","sim","ser","foi","é","são","era","sendo","ter","tem","têm","haver","há",
    "art","artigo","lei","decreto","resolução","acórdão","relator","relatora","turma","câmara",
    "tribunal","stj","stf","tj","trf","ministro","ministra","voto","decisão","processo","recurso",
    "ementa","embargos","embargo","autos","vistos","juiz","juiza","excelencia", "vossa", "senhoria",
    "trata-se", "cuida-se", "ação", "civil", "penal", "apelante", "apelado", "réu", "autor"
}

# =========================
# Lógica Inteligente (NLP Jurídico)
# =========================

def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def smart_summary(text: str) -> str:
    """
    Tenta encontrar a 'causa de pedir' ou o resumo real do caso
    procurando por gatilhos textuais comuns em peças jurídicas.
    """
    normalized = normalize(text)
    lower_text = normalized.lower()
    
    # 1. Tentar pegar a Ementa (Melhor cenário)
    ementa_match = re.search(r"\bementa\b[:\s]*(.*?)(?:\bac[oó]rd[aã]o\b|\brelat[oó]rio\b|$)", lower_text, re.DOTALL)
    if ementa_match and len(ementa_match.group(1)) > 50:
        raw_ementa = normalized[ementa_match.start(1):ementa_match.end(1)]
        return raw_ementa[:800].strip() + ("..." if len(raw_ementa)>800 else "")

    # 2. Se não tem Ementa, procura frases de introdução ("Trata-se de...")
    intro_patterns = [
        r"(trata-se de\s+[\w\s,]+(?:\.))",
        r"(cuida-se de\s+[\w\s,]+(?:\.))",
        r"(o cerne da questão\s+[\w\s,]+(?:\.))",
        r"(a controvérsia cinge-se\s+[\w\s,]+(?:\.))",
        r"(insurge-se a parte\s+[\w\s,]+(?:\.))"
    ]
    
    for pat in intro_patterns:
        match = re.search(pat, lower_text, re.IGNORECASE)
        if match:
            # Pega a frase encontrada e mais um pouco de contexto (300 chars)
            start = match.start()
            end = min(len(normalized), start + 400)
            return "SÍNTESE DETECTADA: " + normalized[start:end].strip() + "..."

    # 3. Fallback: Pega o início, mas limpa cabeçalhos (Ex: Excelentíssimo...)
    # Tenta achar onde começa o texto real (após qualificações)
    lines = normalized.split('\n')
    clean_lines = []
    started = False
    for line in lines:
        # Pula cabeçalhos comuns
        if any(x in line.lower() for x in ["excelentíssimo", "juízo", "vara", "autos nº", "processo nº"]):
            continue
        if len(line) > 50: # Linhas curtas geralmente são sujeira
            clean_lines.append(line)
            
    return "\n".join(clean_lines[:6])[:600] + "..." # Retorna os primeiros 4 parágrafos úteis

def extract_articles_with_context(text: str) -> list[str]:
    """
    Extrai artigos (Ex: Art. 186) e tenta descobrir de qual lei eles são
    olhando o contexto próximo (janela de palavras).
    """
    # Mapeamento de Leis/Códigos
    law_map = {
        "cc": "Código Civil", "civil": "Código Civil", "10406": "Código Civil", "10.406": "Código Civil",
        "cpc": "CPC", "processo civil": "CPC", "13105": "CPC", "13.105": "CPC", "buzaid": "CPC",
        "cp": "Código Penal", "penal": "Código Penal", "2848": "Código Penal",
        "cpp": "CPP", "processo penal": "CPP",
        "clt": "CLT", "trabalho": "CLT", "obreiro": "CLT",
        "cdc": "CDC", "consumidor": "CDC", "8078": "CDC",
        "cf": "Constituição Federal", "constituição": "Constituição Federal", "carta magna": "Constituição Federal",
        "ctn": "CTN", "tributário": "CTN"
    }

    found_citations = []
    
    # Regex para achar "Art. X" ou "Artigo X"
    # Captura o número e eventuais parágrafos/incisos curtos
    article_pattern = re.compile(r"\b(?:art\.?|artigo)\s*(\d+[º°]?[-\w]*)", re.IGNORECASE)
    
    # Varre o texto
    for match in article_pattern.finditer(text):
        article_num = match.group(1)
        start_pos = match.start()
        
        # Cria uma "janela" de contexto: 150 caracteres antes e depois do Artigo
        window_start = max(0, start_pos - 150)
        window_end = min(len(text), start_pos + 150)
        context_window = text[window_start:window_end].lower()
        
        # Tenta identificar a lei no contexto
        detected_law = None
        for key, law_name in law_map.items():
            if key in context_window:
                detected_law = law_name
                break # Achou, para
        
        if detected_law:
            citation = f"Art. {article_num} do {detected_law}"
        else:
            citation = f"Art. {article_num} (Lei não identificada no contexto)"
            
        if citation not in found_citations:
            found_citations.append(citation)
            
    return found_citations[:15] # Limita a 15 para não poluir

def pick_keywords(text: str, k=6):
    clean_text = re.sub(r'[^\w\s]', '', text.lower())
    tokens = [t for t in clean_text.split() if t not in STOPWORDS_PT and len(t) > 3 and not t.isdigit()]
    if not tokens: return []
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(k)]

def build_smart_search_queries(keywords: list[str], citations: list[str], text_snippet: str) -> list[str]:
    """
    Cria queries de pesquisa que funcionam de verdade no Google/Jusbrasil.
    """
    queries = []
    
    # 1. Query Baseada em Artigo + Palavra Chave (Muito forte)
    # Pega a primeira citação que tenha Lei identificada (não 'Lei não identificada')
    best_citation = next((c for c in citations if "Lei não" not in c), None)
    main_keyword = keywords[0] if keywords else "jurisprudência"
    
    if best_citation:
        # Remove "do" e "da" para ficar limpo: "Art. 186 Código Civil Dano Moral"
        clean_cit = best_citation.replace("do ", "").replace("da ", "")
        queries.append(f"Jurisprudência {clean_cit} {main_keyword}")
        queries.append(f"Comentários {clean_cit}")

    # 2. Query Baseada em Tese/Tema
    if keywords:
        # Junta as 3 principais palavras: "Dano Moral Indenização Atraso Voo"
        topic = " ".join(keywords[:3])
        queries.append(f"{topic} STJ") # STJ é ótimo para teses cíveis
        queries.append(f"{topic} Acórdão recente")

    # 3. Query Procedural (se houver menção a recurso)
    if "recurso" in text_snippet.lower() or "agravo" in text_snippet.lower():
        queries.append(f"Prazo e requisitos {main_keyword} Novo CPC")

    return queries[:5]

def build_output(text: str):
    # 1. Normaliza
    text = normalize(text)
    
    # 2. Resumo Inteligente (Melhoria Solicitada)
    resumo = smart_summary(text)
    
    # 3. Palavras-Chave
    keywords = pick_keywords(text, k=6)
    tema_principal = f"{', '.join(keywords[:3]).title()}" if keywords else "Análise Jurídica"

    # 4. Fundamentação Rica (Melhoria Solicitada: Artigo + Lei)
    fundamentos_normas = extract_articles_with_context(text)
    
    # 5. Jurisprudência (Busca por Súmulas e Temas)
    juris_refs = re.findall(r"(Súmula\s*\d+|Tema\s*\d+|REsp\s*[\d\.]+)", text, re.IGNORECASE)
    juris_refs = list(set([j.replace("\n", " ") for j in juris_refs])) # Remove duplicatas

    # 6. Pesquisa para Advogado (Melhoria Solicitada)
    pesquisas = build_smart_search_queries(keywords, fundamentos_normas, resumo)
    
    # 7. Sugestões de Biblioteca
    sugestoes = [l for l in LIBRARY_LINKS if l['key'] in ['CF_HTML', 'CPC', 'CC']] 

    return {
        "tema_principal": tema_principal,
        "resumo": resumo,
        "fundamentos_normas": fundamentos_normas,
        "fundamentos_juris": juris_refs,
        "keywords": keywords,
        "queries_juris": pesquisas,
        "sugestoes": sugestoes,
        "alerta": "Texto curto - análise limitada" if len(text) < 500 else None
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
        tipo_peca="Documento Jurídico"
    )
    db.session.add(nova)
    db.session.commit()

    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

@app.route("/resultado/<int:id>")
def resultado(id):
    analise = Analise.query.get_or_404(id)
    out = build_output(analise.texto_original)
    return render_template("resultado.html", out=out, texto=analise.texto_original, now=datetime.now(), analise_id=analise.id)

@app.route("/historico")
def historico():
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
def biblioteca(): return render_template("biblioteca.html", links=LIBRARY_LINKS)

@app.errorhandler(404)
def page_not_found(e): return render_template('404.html'), 404
@app.errorhandler(500)
def server_error(e): return render_template('500.html'), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
