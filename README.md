# RepoAnalytics — Ferramenta de Acompanhamento de Projetos (FAP)

[![lint](https://github.com/bryan7paz/repoanalytics/actions/workflows/lint.yml/badge.svg)](https://github.com/bryan7paz/repoanalytics/actions/workflows/lint.yml)

Plataforma de **gestão de repositórios de software** para gestores de equipe:
você cadastra o repositório do time e acompanha quem mais comita, quem mais
adiciona e apaga linhas, os cinco melhores contribuidores e a saúde do projeto —
via **Mineração de Repositórios de Software (MSR)**: coleta code churn
(PyDriller) e métricas sociais (GitHub API), armazena em PostgreSQL e apresenta
um dashboard com Plotly.js.

**Para quem é:** gestores de equipe que precisam acompanhar como o time
trabalha — não uma pessoa comum. Você entra com GitHub (OAuth), cadastra os
repositórios que quer acompanhar e a plataforma coleta commits, releases e
contribuidores em segundo plano, calculando métricas que o próprio GitHub não
mostra (Bus Factor, TTFR, churn relativo) mais **análises exclusivas**
(curva de concentração de conhecimento e score de sustentabilidade 0–100).

## Passo a passo (do zero)

1. **Baixar**: `git clone https://github.com/bryan7paz/repoanalytics.git`
   (ou **Code → Download ZIP** no GitHub e extrair)
2. **Instalar**: dentro da pasta, `python setup.py` — cria o venv, instala as
   dependências, gera o `.env` (com `SESSION_SECRET` pronto), sobe o
   PostgreSQL, cria o banco `fap` e aplica o schema
3. **Preencher** (o setup avisa o que falta): `DB_PASSWORD` (a senha do
   postgres escolhida na instalação) e `GITHUB_TOKEN`
   (github.com/settings/tokens)
4. **Abrir**: duplo clique em `rodar.bat` (sobe o postgres se caiu + inicia o
   app) — ou `cd src` e `..\fap_env\Scripts\python.exe app.py`
5. **Usar**: abra `http://127.0.0.1:5000`, entre (GitHub ou modo dev), cole a
   URL de um repositório público e acompanhe a coleta em background

## Fluxo
1. `GET /` sem sessão → redireciona para `/login` (OAuth GitHub; sem
   `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` no `.env` o botão cai no
   `/login/dev`, que entra como usuário `dev` para desenvolvimento).
2. No dashboard, cole a URL de um repositório público → valida na API do GitHub,
   vincula ao usuário e dispara a coleta em background (polling em
   `/api/coleta/status`).
3. Na página do repositório, duas abas:
   - **GitHub**: commits por dia com seletor de período (1 dia, 7 dias, 3
     meses ou 6 meses), linhas +/-, autores do período, quem faz o quê
     (linhas por autor), contribuidores e releases (API ao vivo);
   - **Análises FAP**: score 0–100 com barras de componentes e alertas
      explicáveis, evolução do score, comparação entre duas coletas do
      mesmo repositório, curva de concentração (top-1 e top-5 por mês) e
      métricas de sustentabilidade. O botão do relatório `.docx` fica no
      topo da página.
   4. No dashboard, marque 2+ repositórios para **comparar** (`/comparar`) ou
      abra o **snapshot** (`/snapshot`): tabela consolidada por período coletado,
      com números fixos e export CSV e XML.

## Estrutura
```
fap/
├── requirements.txt
├── setup.py                    # instalação guiada (venv, .env, banco, schema)
├── rodar.bat                   # sobe o PostgreSQL + inicia o app (Windows)
├── Dockerfile                  # imagem (python:3.12-slim + git p/ PyDriller)
├── docker-compose.yml          # app + postgres:16 com volumes
├── .env.example                # -> copie para .env e preencha
├── conftest.py + pytest.ini    # raiz da suíte de testes (100)
├── .github/workflows/lint.yml  # CI: pyflakes + pytest (PostgreSQL)
├── sql/schema.sql              # idempotente: métricas, usuários e vínculos
├── data/                       # clones git dos repositórios minerados (gitignored)
├── src/
│   ├── config.py               # carrega variáveis do .env
│   ├── database.py             # pool de conexões + ETL (upsert) + init_schema()
│   ├── status.py               # estado da coleta em background (thread-safe)
│   ├── analises.py             # curva de concentração + score de sustentabilidade
│   ├── relatorio.py            # relatório .docx (python-docx + matplotlib)
│   ├── app.py                  # Flask: OAuth, cadastro/edição de repos, APIs, snapshot e coleta
│   └── collect/
│       ├── pydriller_collect.py   # code churn + Bus Factor + churn relativo
│       │                          # + agregações por dia, mês e autor
│       └── github_metrics.py      # TTFR (mediana, sem bots) + issues + releases
│                                  # + contribuidores (com retry e token)
├── templates/
│   ├── base.html               # topo (marca + usuário) e rodapé
│   ├── login.html              # cartão de autenticação
│   ├── meus_repos.html         # formulário + lista com status/polling
│   ├── repo_detalhe.html       # abas GitHub | Análises FAP + relatório
│   ├── comparar.html           # comparação lado a lado
│   └── snapshot.html           # snapshot consolidado por período
├── tests/                      # 102 testes: rotas, coletores, banco, score...
└── static/
    ├── css/style.css           # token block (IBM Plex, tema claro)
    └── js/
        ├── repos.js            # cadastro/edição + polling + seleção p/ comparar
        ├── comparar.js         # gráficos da comparação
        ├── repo_detalhe.js     # gráficos Plotly + score + abas
        └── plotly-2.35.2.min.js # Plotly local (funciona offline)
```

## Requisitos
-   Python 3.12 ou 3.13 (com "Add to PATH" — versões mais novas podem falhar
    ao compilar dependências presas no `requirements.txt`)
-   PostgreSQL (porta 5432)
-   Token do GitHub (essencial na prática: sem ele são só 60 req/hora da API e
    o cálculo do TTFR faz 1 requisição por issue — a coleta não fecha a tempo;
    com token autenticado o limite é 5.000 req/hora)
-   OAuth App do GitHub (opcional; habilita o login real)

## Instalação

### Instalação rápida (recomendada)
```bash
python setup.py
```
Faz tudo sozinho: cria o venv, instala as dependências, gera o `.env` (com
`SESSION_SECRET` pronto), sobe o PostgreSQL se estiver parado, cria o banco
`fap`, aplica o schema e imprime o que ainda falta (token do GitHub, OAuth
App). Depois, para rodar com duplo clique:
```bash
rodar.bat   # sobe o postgres se caiu e inicia o app
```

### Instalação manual
1. Crie o ambiente virtual e instale as dependências:
   ```bash
   python -m venv fap_env
   .\fap_env\Scripts\activate
   pip install -r requirements.txt
   ```
2. Crie o banco (o schema é aplicado sozinho no boot):
   ```bash
   psql -U postgres -d postgres -c "CREATE DATABASE fap;"
   ```
3. Preencha as credenciais:
   ```bash
   copy .env.example .env   # edite DB_PASSWORD (e GITHUB_TOKEN)
   ```

### Variáveis do `.env`
| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | sim | acesso ao PostgreSQL (defaults: localhost/5432/fap/postgres) |
| `GITHUB_TOKEN` | essencial | PAT do GitHub: sem ele são só 60 req/h e a coleta de TTFR não fecha |
| `SESSION_SECRET` | sim | segredo da sessão Flask e da cifra do token OAuth (sem ele o token não é armazenado) |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | para login real | credenciais do OAuth App (sem elas o `/login` perde o botão) |
| `MESES_ANALISE` | não | janela de análise em meses (default: 6) |
| `MINERACAO_INTERVALO_DIAS` | não | período da coleta automática (default: 7) |
| `DB_CONNECT_TIMEOUT` | não | timeout de conexão com o banco em segundos (default: 10) |
| `FAP_HOST` / `PORT` | não | bind do servidor waitress (defaults: 127.0.0.1/5000) |
| `FAP_SEM_AUTOCOLETA` | não | `1` = não coleta nada no boot (usado pelos testes) |

### Login com GitHub (OAuth App)
1. Em <https://github.com/settings/developers> → **OAuth Apps** → **New OAuth App**
2. Preencha:
   - **Homepage URL**: `http://127.0.0.1:5000`
   - **Authorization callback URL**: `http://127.0.0.1:5000/callback`
3. Copie o **Client ID** e gere o **Client Secret** e cole no `.env`
4. Reinicie o app — o botão **"Entrar com GitHub"** aparece no `/login`

Sem OAuth App, o `/login` mostra o botão **"Entrar (modo desenvolvimento)"**
(`/login/dev`), que entra como usuário `dev` — a rota só existe em localhost e
só enquanto o OAuth não está configurado.

## Executar
```bash
cd src
..\fap_env\Scripts\python.exe app.py
```
(o venv fica na raiz do projeto — ou ative-o com `..\fap_env\Scripts\activate`
e use `python app.py`)
Abra `http://localhost:5000`. Os repositórios vinculados que ainda não foram
coletados (`Repositorio.atualizado_em IS NULL`) são processados em background no
boot, e uma rotina APScheduler (padrão: 7 dias) mantém tudo atualizado.

### Com Docker (opcional)
Com o Docker Desktop instalado e o `.env` preenchido:
```bash
docker compose up --build
```
Sobe app + PostgreSQL na mesma rede (o banco é criado sozinho pelo compose) e o
app abre em `http://localhost:5000`. Os clones ficam num volume `dados-fap` e o
banco num volume `postgres-dados` (sobrevivem a `docker compose down`).

Endpoints principais:
- `POST /repos` / `PUT /repos/<id>` — cadastro e edição dos repositórios do usuário
- `GET /api/repos` — lista JSON (polling do dashboard)
- `GET /api/repo/<id>/resumo` — série, autores, métricas, curva, histórico do
  score e score
- `GET /api/repo/<id>/github` — contribuidores e releases (API ao vivo)
- `GET /api/coleta/status` — estado da coleta
- `GET /comparar?ids=1,2` — comparação lado a lado dos repositórios
- `GET /snapshot` — tabela consolidada por período coletado (avaliável)
- `GET /snapshot.csv?periodo=AAAA-MM-DD|AAAA-MM-DD` — export CSV do snapshot
- `GET /snapshot.xml?periodo=AAAA-MM-DD|AAAA-MM-DD` — export XML do snapshot
- `GET /api/repo/<id>/periodos` — períodos coletados do repositório
  (base da comparação entre versões do mesmo projeto)
- `GET /repo/<id>/relatorio` — relatório `.docx` (score, métricas, gráficos)
- `POST /logout` — encerra a sessão (somente POST)
- `GET /api/health` — verificação de vida

## Coleta manual (opcional)
Com o banco criado, basta reiniciar o app que ele detecta os
repositórios pendentes e coleta; para rodar os motores fora do app:
```bash
cd src
python -m collect.pydriller_collect    # commits, Bus Factor, churn relativo, autores/mês
python -m collect.github_metrics       # TTFR, issues, releases, contribuidores
```

## Métricas
| Métrica | Definição |
|---------|-----------|
| Commits | total de commits de autores humanos na janela de coleta |
| Bus Factor | menor `k` tal que a soma das `k` maiores contribuições > 50% do total |
| TTFR | mediana do tempo até a primeira resposta humana (exclui PRs e bots) |
| Churn relativo | (linhas add + del no período) / LOC do repositório |
| Cadência de Releases | releases publicados por mês na janela (`R / M`) |
| Contribuidores ativos | pessoas distintas que abriram ou comentaram issues no período (bots fora) |
| Curva de concentração | % dos commits do mês feitos pelo top-1 e top-5 de autores |
| Score (0–100) | média das componentes normalizadas: atividade (teto 1000 commits), Bus Factor (teto 5), responsividade (piso 7 dias de TTFR) e estabilidade (piso de churn 1,5); métricas ausentes não entram na média |

Nota metodológica: commits e Bus Factor consideram somente autores humanos —
contas automatizadas (bots, como `dependabot[bot]`) são filtradas na coleta,
conforme a prática dos estudos de Truck Factor.

## Testes
```bash
pytest -q        # na raiz do projeto (precisa do PostgreSQL; CI roda os mesmos)
```
Suíte (102 testes): score/curva (matemática pura), utilitários dos coletores,
rede mockada com `responses` (paginação, PRs, bots, rate limit, releases),
helpers do banco (criptografia do token e upserts), autocoleta, relatório
`.docx` e snapshot, e smoke das rotas com login simulado.

## Licença
Distribuído sob a licença [MIT](LICENSE).
