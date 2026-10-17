# Monitor de Preços

Visita páginas de produtos de tempos em tempos, guarda o histórico de preços e te avisa quando o preço cai.

## Instalação

```bash
pip install requests beautifulsoup4 plyer
```

## Configurar produtos (`produtos.json`)

```json
{
  "intervalo_minutos": 60,
  "alertas": { "desktop": true, "telegram": false, "email_para": "" },
  "produtos": [
    {
      "nome": "Fone XYZ",
      "url": "https://www.mercadolivre.com.br/...",
      "preco_alvo": 199.90,
      "seletor": ""
    }
  ]
}
```

- `preco_alvo` (opcional): avisa quando o preço chegar nesse valor ou abaixo.
- `seletor` (opcional): seletor CSS do elemento do preço. Só é necessário se o programa não achar o preço sozinho.
  Para descobrir: abra a página no navegador, clique com o botão direito no preço → **Inspecionar** e veja a classe/id do elemento (ex.: `span.preco-final`).

Sem `seletor`, o programa tenta: seletores conhecidos (Amazon, Mercado Livre, Magalu, KaBuM), dados estruturados JSON-LD (`schema.org/Product`) e meta tags de preço, que a maioria das lojas tem.

## Quando você recebe alerta

- o preço caiu em relação à última leitura;
- o preço atingiu o `preco_alvo`;
- é o menor preço já registrado.

## Uso

```bash
python monitor.py               # roda em loop
python monitor.py --uma-vez     # verifica uma vez e sai
python monitor.py --intervalo 30
python monitor.py --historico   # mostra o histórico de preços
```

O histórico fica em `historico.json`.

## Alertas

- **Desktop**: notificação do Windows (padrão).
- **Telegram**: crie um bot com o @BotFather, ponha `"telegram": true` e defina as variáveis de ambiente
  `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
- **E-mail**: preencha `email_para` e defina `EMAIL_USUARIO` e `EMAIL_SENHA` (no Gmail, use uma *senha de app*).
  Servidor padrão: `smtp.gmail.com` (troque com `EMAIL_SMTP`).

## Rodar automaticamente no Windows

Em vez de deixar o terminal aberto, use o Agendador de Tarefas:

```bash
schtasks /create /tn "MonitorPrecos" /sc hourly /tr "python C:\caminho\monitor.py --uma-vez"
```

## Limitações

- Lojas que montam o preço via JavaScript ou que bloqueiam robôs (a Amazon, às vezes) podem não responder.
  Nesses casos dá para trocar o `requests` pelo Playwright.
- Respeite os termos de uso dos sites e não use intervalos curtos demais.
