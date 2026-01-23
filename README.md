# Lumen Jurídico (MVP local)

Clareza sobre decisões judiciais.

## Como rodar (Windows / CMD)
1. Instale Python 3.10+
2. Abra o CMD na pasta do projeto
3. Crie e ative um ambiente virtual:

```bat
python -m venv .venv
.venv\Scripts\activate
```

4. Instale dependências:

```bat
pip install -r requirements.txt
```

5. Rode o app:

```bat
python app.py
```

6. Acesse no navegador:
http://127.0.0.1:5000

## Config (opcional)
Crie um arquivo `.env` na raiz:
```
SECRET_KEY=uma-chave-bem-grande-aqui
```
