"""
Monitor de preços: visita páginas de produtos de tempos em tempos,
extrai o preço e avisa quando ele cai.

Uso:
    python monitor.py                 # roda em loop (intervalo do produtos.json)
    python monitor.py --uma-vez       # verifica uma vez e sai (bom p/ Agendador de Tarefas)
    python monitor.py --intervalo 30  # loop a cada 30 minutos
    python monitor.py --historico     # mostra o histórico de preços
"""

import argparse
import json
import os
import random
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PASTA = Path(__file__).resolve().parent
ARQ_PRODUTOS = PASTA / "produtos.json"
ARQ_HISTORICO = PASTA / "historico.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Seletores CSS conhecidos por loja (usados se o produto não definir "seletor").
SELETORES_POR_SITE = {
    "amazon.": ["#corePrice_feature_div .a-offscreen", ".a-price .a-offscreen", "#priceblock_ourprice"],
    "mercadolivre.": ["meta[itemprop=price]", ".ui-pdp-price__second-line .andes-money-amount__fraction"],
    "magazineluiza.": ["[data-testid=price-value]"],
    "kabum.": ["h4.finalPrice", ".finalPrice"],
}


# ---------------------------------------------------------------- extração

def texto_para_preco(texto):
    """Converte 'R$ 1.299,90', '1299.90', '1,299.90' etc. em float."""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    s = re.sub(r"[^\d,.]", "", str(texto))
    if not s:
        return None
    if "," in s and "." in s:
        # o separador que aparece por último é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        inteiro, _, dec = s.rpartition(",")
        s = f"{inteiro.replace(',', '')}.{dec}" if len(dec) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[1]) == 3):
        s = s.replace(".", "")  # "1.299" = mil duzentos e noventa e nove
    try:
        return float(s)
    except ValueError:
        return None


def _preco_do_elemento(el):
    valor = el.get("content") or el.get("value") or el.get_text(" ", strip=True)
    return texto_para_preco(valor)


def _preco_json_ld(soup):
    """Procura dados estruturados schema.org (Product -> offers -> price)."""
    def procurar(obj):
        if isinstance(obj, list):
            for item in obj:
                p = procurar(item)
                if p is not None:
                    return p
        elif isinstance(obj, dict):
            for chave in ("price", "lowPrice"):
                if chave in obj:
                    p = texto_para_preco(obj[chave])
                    if p:
                        return p
            for chave in ("offers", "@graph", "mainEntity"):
                if chave in obj:
                    p = procurar(obj[chave])
                    if p is not None:
                        return p
        return None

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            p = procurar(json.loads(tag.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if p:
            return p
    return None


def extrair_preco(html, url="", seletor=None):
    """Tenta, em ordem: seletor do usuário, seletor da loja, JSON-LD, meta tags."""
    soup = BeautifulSoup(html, "html.parser")

    seletores = [seletor] if seletor else []
    for dominio, lista in SELETORES_POR_SITE.items():
        if dominio in url:
            seletores += lista
    for sel in seletores:
        el = soup.select_one(sel)
        if el:
            p = _preco_do_elemento(el)
            if p:
                return p

    p = _preco_json_ld(soup)
    if p:
        return p

    for sel in ('meta[property="product:price:amount"]', 'meta[property="og:price:amount"]',
                '[itemprop="price"]'):
        el = soup.select_one(sel)
        if el:
            p = _preco_do_elemento(el)
            if p:
                return p
    return None


def buscar_preco(produto):
    resp = requests.get(produto["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return extrair_preco(resp.text, produto["url"], produto.get("seletor"))


# ---------------------------------------------------------------- alertas

def notificar(titulo, mensagem, config):
    print(f"\n🔔 {titulo}\n   {mensagem}\n")
    alertas = config.get("alertas", {})

    if alertas.get("desktop", True):
        try:
            from plyer import notification
            notification.notify(title=titulo, message=mensagem[:250], timeout=10)
        except Exception as e:
            print(f"   (notificação desktop falhou: {e})")

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if alertas.get("telegram") and token and chat_id:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": f"{titulo}\n{mensagem}"}, timeout=15)
        except requests.RequestException as e:
            print(f"   (Telegram falhou: {e})")

    remetente = os.getenv("EMAIL_USUARIO")
    senha = os.getenv("EMAIL_SENHA")
    destino = alertas.get("email_para")
    if destino and remetente and senha:
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = titulo, remetente, destino
            msg.set_content(mensagem)
            with smtplib.SMTP_SSL(os.getenv("EMAIL_SMTP", "smtp.gmail.com"), 465, timeout=20) as s:
                s.login(remetente, senha)
                s.send_message(msg)
        except Exception as e:
            print(f"   (e-mail falhou: {e})")


# ---------------------------------------------------------------- histórico

def carregar_json(caminho, padrao):
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return padrao


def salvar_historico(historico):
    ARQ_HISTORICO.write_text(json.dumps(historico, indent=2, ensure_ascii=False), encoding="utf-8")


def brl(valor):
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- ciclo

def verificar_todos(config, historico):
    for produto in config.get("produtos", []):
        nome, url = produto["nome"], produto["url"]
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            preco = buscar_preco(produto)
        except requests.RequestException as e:
            print(f"[{agora}] ⚠️  {nome}: erro ao acessar a página ({e})")
            continue
        if preco is None:
            print(f"[{agora}] ⚠️  {nome}: preço não encontrado. Defina um \"seletor\" CSS para esse produto.")
            continue

        registros = historico.setdefault(url, [])
        anterior = registros[-1]["preco"] if registros else None
        menor = min((r["preco"] for r in registros), default=None)
        registros.append({"data": agora, "preco": preco})
        salvar_historico(historico)

        alvo = produto.get("preco_alvo")
        info = f"{brl(preco)}" + (f" (antes {brl(anterior)})" if anterior else " (primeira leitura)")
        print(f"[{agora}] {nome}: {info}")

        motivos = []
        if anterior is not None and preco < anterior:
            queda = (anterior - preco) / anterior * 100
            motivos.append(f"caiu {queda:.1f}% ({brl(anterior)} → {brl(preco)})")
        if alvo and preco <= alvo and (anterior is None or anterior > alvo):
            motivos.append(f"atingiu seu preço-alvo de {brl(alvo)}")
        if menor is not None and preco < menor:
            motivos.append("é o menor preço já registrado")

        if motivos:
            notificar(f"Preço baixou: {nome}", f"{brl(preco)} — " + "; ".join(motivos) + f"\n{url}", config)

        time.sleep(random.uniform(2, 5))  # não bombardear as lojas


def mostrar_historico(historico, config):
    nomes = {p["url"]: p["nome"] for p in config.get("produtos", [])}
    if not historico:
        print("Nenhum histórico ainda.")
    for url, registros in historico.items():
        precos = [r["preco"] for r in registros]
        print(f"\n{nomes.get(url, url)}")
        print(f"  leituras: {len(precos)} | menor: {brl(min(precos))} | maior: {brl(max(precos))} "
              f"| atual: {brl(precos[-1])}")
        for r in registros[-10:]:
            print(f"   {r['data']}  {brl(r['preco'])}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # emojis no terminal do Windows

    ap = argparse.ArgumentParser(description="Monitor de preços de e-commerce")
    ap.add_argument("--uma-vez", action="store_true", help="verifica uma vez e sai")
    ap.add_argument("--intervalo", type=float, help="minutos entre verificações")
    ap.add_argument("--historico", action="store_true", help="mostra o histórico e sai")
    args = ap.parse_args()

    config = carregar_json(ARQ_PRODUTOS, None)
    if not config:
        sys.exit(f"Crie o arquivo {ARQ_PRODUTOS.name} (veja o README).")
    historico = carregar_json(ARQ_HISTORICO, {})

    if args.historico:
        mostrar_historico(historico, config)
        return

    intervalo = args.intervalo or config.get("intervalo_minutos", 60)
    while True:
        verificar_todos(config, historico)
        if args.uma_vez:
            break
        print(f"Próxima verificação em {intervalo:g} min (Ctrl+C para sair)...")
        try:
            time.sleep(intervalo * 60)
        except KeyboardInterrupt:
            print("\nEncerrado.")
            break


if __name__ == "__main__":
    main()
