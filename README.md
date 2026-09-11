# Central 360

Painel de clima, pesca, futebol e notícias em uma única página estática (`index.html`), atualizado automaticamente a cada 6 horas por um workflow do GitHub Actions — sem depender de nenhum serviço externo pago.

## O que tem na página

- **Animação** — 17 quadros de precipitação acumulada em 24h (modelo COSMO 7×7km, área Brasil), extraídos do [VIME/INMET](https://vime.inmet.gov.br/).
- **Previsão 7 dias** — ClimaTempo, cidade de Piracicaba-SP.
- **Calendário Lunar** — fases da lua calculadas astronomicamente (sem depender de terceiros), com dias marcados como "★ Melhor" (lua nova/cheia) e "Técnica" (quartos).
- **Corinthians** — posição, pontos, aproveitamento, saldo de gols e os 3 próximos jogos, extraídos do ge.globo.
- **Tabela dos Campeonatos** — classificação completa do Brasileirão Série A.
- **Notícias do Mundo** — 6 manchetes do g1.globo/mundo.

## Como funciona

`scraper/scrape.py` roda a cada 6h via GitHub Actions (`.github/workflows/update.yml`), busca dados reais de cada fonte, preenche `template.html` e sobrescreve `index.html`. Se uma fonte falhar (site fora do ar, mudança de layout, etc.), a seção correspondente **mantém o último valor válido já publicado** em vez de quebrar a página ou inventar dados — cada seção fica marcada com um comentário `<!--S:NOME-->...<!--E:NOME-->` (ou `/*S:NOME*/.../*E:NOME*/` dentro do `<script>`) que o próprio script usa para recuperar o valor anterior.

Fontes:
- INMET/VIME — precisa de navegador real (Playwright/Chromium), é uma aplicação React que só desenha os mapas via JavaScript.
- ClimaTempo, ge.globo (tabela e agenda do Corinthians) — HTML estático, lido direto via `requests` (mais rápido e mais estável).
- g1.globo/mundo — feed renderizado no cliente, também via Playwright.

## Configuração inicial (uma vez só)

1. **Ative o GitHub Pages**: Settings → Pages → Source: "Deploy from a branch" → Branch: `main` / `(root)`. A URL pública fica em `https://<seu-usuário>.github.io/Central-360/`.
2. O workflow já tem `permissions: contents: write`, então não precisa criar nenhum token — ele usa o `GITHUB_TOKEN` automático do repositório para commitar o `index.html` atualizado.

## Rodando localmente

```bash
cd scraper
pip install -r requirements.txt
python -m playwright install chromium
python scrape.py
```

Isso gera/atualiza `index.html` na raiz do repositório. Abra o arquivo direto no navegador para conferir antes de commitar.

## Disparar uma atualização manual

Na aba **Actions** do repositório, escolha o workflow "Atualizar Central 360" e clique em **Run workflow**.
