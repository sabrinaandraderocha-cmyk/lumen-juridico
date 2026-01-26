import os
import re
from collections import Counter
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

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
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
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
# Dados Estáticos (Biblioteca e Dicionário)
# =========================
LIBRARY_LINKS = [
    {"key": "CF_HTML", "titulo": "Constituição Federal", "url": "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm", "tipo": "Constituição"},
    {"key": "CC", "titulo": "Código Civil", "url": "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm", "tipo": "Código"},
    {"key": "CPC", "titulo": "Código de Processo Civil (CPC)", "url": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm", "tipo": "Código"},
    {"key": "CP", "titulo": "Código Penal (CP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm", "tipo": "Código"},
    {"key": "CPP", "titulo": "Código de Processo Penal (CPP)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm", "tipo": "Código"},
    {"key": "CLT", "titulo": "Consolidação das Leis do Trabalho (CLT)", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm", "tipo": "Trabalhista"},
    {"key": "CDC", "titulo": "Código de Defesa do Consumidor", "url": "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", "tipo": "Consumidor"},
    {"key": "CTN", "titulo": "Código Tributário Nacional", "url": "https://www.planalto.gov.br/ccivil_03/leis/l5172.htm", "tipo": "Tributário"},
]

# Dados para o GLOSSÁRIO (Termo: Explicação Simples)
GLOSSARY_DICT = {
    "acórdão": "Decisão final proferida por um tribunal (grupo de juízes).",
    "prescrição": "Perda do direito de punir ou cobrar algo devido ao passar do tempo.",
    "decadência": "Perda do próprio direito pelo não exercício no prazo legal.",
    "ementa": "Resumo oficial de uma decisão judicial.",
    "litispendência": "Quando existem dois processos iguais rodando ao mesmo tempo.",
    "tempestivo": "Que foi feito dentro do prazo legal.",
    "precluir": "Perder a oportunidade de falar no processo por ter perdido o prazo.",
    "agravo": "Tipo de recurso contra decisões específicas durante o processo.",
    "trânsito em julgado": "Quando não cabe mais nenhum recurso; a decisão é definitiva."
}

# =========================
# Lógica de Inteligência
# =========================

def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def extract_articles_with_context(text: str) -> list[str]:
    law_map = {
        "cc": "Código Civil", "civil": "Código Civil", "10.406": "Código Civil",
        "cpc": "CPC", "processo civil": "CPC", "13.105": "CPC",
        "cp": "Código Penal", "penal": "Código Penal", "2.848": "Código Penal",
        "cpp": "CPP", "processo penal": "CPP",
        "cdc": "CDC", "consumidor": "CDC", "8.078": "CDC",
        "cf": "Constituição Federal", "constituição": "Constituição Federal",
        "clt": "CLT", "trabalho": "CLT"
    }
    found_citations = []
    article_pattern = re.compile(r"\b(?:art\.?|artigo)\s*(\d+[º°]?[-\w]*)", re.IGNORECASE)
    
    for match in article_pattern.finditer(text):
        article_num = match.group(1)
        start_pos = match.start()
        window = text[max(0, start_pos - 150):min(len(text), start_pos + 150)].lower()
        detected_law = "Lei não identificada"
        for key, law_name in law_map.items():
            if key in window:
                detected_law = law_name
                break
        citation = f"Art. {article_num} do {detected_law}"
        if citation not in found_citations:
            found_citations.append(citation)
    return found_citations[:15]

def generate_checklist(text: str) -> list[str]:
    """Nova lógica para gerar o Checklist Prático."""
    text_lower = text.lower()
    checklist = []
    
    # Critérios de verificação baseados em palavras-chave
    if "recurso" in text_lower or "apelação" in text_lower:
        checklist.append("Verificar tempestividade (prazo do recurso).")
        checklist.append("Conferir preparo recursal (custas pagas).")
    
    if "prescrição" in text_lower:
        checklist.append("Calcular marco interruptivo da prescrição.")
        checklist.append("Analisar se houve prescrição intercorrente.")
    
    if "acórdão" in text_lower:
        checklist.append("Analisar se há omissão ou contradição para Embargos.")
    
    if "indenização" in text_lower or "dano" in text_lower:
        checklist.append("Verificar prova do nexo causal e do dano efetivo.")
    
    # Fallback se não identificar nada específico
    if not checklist:
        checklist = ["Revisar fundamentação legal citada.", "Verificar assinatura das partes/advogados."]
        
    return checklist

def generate_glossary(text: str) -> list[dict]:
    """Nova lógica para gerar o Glossário Dinâmico."""
    text_lower = text.lower()
    found_terms = []
    
    for term, definition in GLOSSARY_DICT.items():
        if term in text_lower:
            found_terms.append({"termo": term.title(), "definicao": definition})
            
    return found_terms

def build_output(text: str):
    text_norm = normalize(text)
    
    # 1. Resumo (pega os primeiros 600 caracteres ou ementa)
    ementa_match = re.search(r"\bementa\b[:\s]*(.*?)(?:\bac[oó]rd[aã]o\b|\brelat[oó]rio\b|$)", text_norm.lower(), re.DOTALL)
    resumo = text_norm[ementa_match.start(1):ementa_match.end(1)].strip()[:800] if ementa_match else text_norm[:600] + "..."

    # 2. Fundamentos e Palavras-Chave
    fundamentos = extract_articles_with_context(text_norm)
    
    # 3. CHECKLIST (Ativado agora)
    checklist = generate_checklist(text_norm)
    
    # 4. GLOSSÁRIO (Ativado agora)
    glossario = generate_glossary(text_norm)

    return {
        "tema_principal": "Análise Jurídica",
        "resumo": resumo,
        "fundamentos_normas": fundamentos,
        "checklist": checklist,
        "glossario": glossario,
        "sugestoes": LIBRARY_LINKS[:6]
    }

# =========================
# Rotas Flask (Simplificadas para o Deploy)
# =========================

@app.route("/")
def home():
    historico = Analise.query.order_by(Analise.data_criacao.desc()).limit(5).all()
    return render_template("index.html", historico=historico)

@app.route("/analisar", methods=["POST"])
def analisar():
    texto = request.form.get("texto", "").strip()
    arquivo = request.files.get("arquivo")
    
    # Lógica de extração de texto (PDF/Docx) aqui...
    # (Mantive simplificado para focar na sua dúvida dos campos vazios)
    
    out = build_output(texto)
    nova = Analise(titulo_resumo=out["tema_principal"], texto_original=texto)
    db.session.add(nova)
    db.session.commit()

    return render_template("resultado.html", out=out, texto=texto, now=datetime.now(), analise_id=nova.id)

# Outras rotas (biblioteca, histórico, etc)...
@app.get("/biblioteca")
def biblioteca():
    return render_template("biblioteca.html", links=LIBRARY_LINKS)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
