# RepoAnalytics

[![lint](https://github.com/bryan7paz/repoanalytics/actions/workflows/lint.yml/badge.svg)](https://github.com/bryan7paz/repoanalytics/actions/workflows/lint.yml)

Ferramenta de Acompanhamento de Projetos (FAP): plataforma de gestão de repositórios de software para gestores de equipe. Você cadastra o repositório do time e acompanha quem mais comita, quem mais adiciona e apaga linhas, os cinco melhores contribuidores e a saúde do projeto.

Os dados vêm de Mineração de Repositórios de Software (MSR): o code churn é coletado com PyDriller, as métricas sociais com a API do GitHub, tudo fica em PostgreSQL e o dashboard usa Plotly.js.

O público-alvo são gestores que precisam acompanhar como o time trabalha. Você entra com o GitHub (OAuth), cadastra os repositórios e a coleta de commits, releases e contribuidores roda em segundo plano. Além de métricas que o GitHub não mostra (Bus Factor, TTFR, churn relativo), há duas análises próprias: a curva de concentração de conhecimento e um score de sustentabilidade de 0 a 100.

## Sumário

- [Começando](#começando)
- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Executando](#executando)
- [Endpoints](#endpoints)
- [Métricas](#métricas)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Testes](#testes)
- [Licença](#licença)

## Começando

```bash
git clone https://github.com/bryan7paz/repoanalytics.git
cd repoanalytics
python setup.py
```

O `setup.py` cria o venv, instala as dependências, gera o `.env` (com `SESSION_SECRET` pronto), sobe o PostgreSQL, cria o banco `fap` e aplica o schema. Ao final ele avisa o que ainda falta preencher: `DB_PASSWORD` (a senha do postgres escolhida na instalação) e `GITHUB_TOKEN` (gere em https://github.com/settings/tokens).

Depois é só abrir o `rodar.bat` (sobe o postgres se ele tiver caído e inicia o app) e acessar http://127.0.0.1:5000. Entre com o GitHub ou pelo modo de desenvolvimento, cole a URL de um repositório público e acompanhe a coleta.

## Como funciona

1. `GET /` sem sessão redireciona para `/login`. Se `GITHUB_CLIENT_ID` e `GITHUB_CLIENT_SECRET` não estiverem no `.env`, o botão leva a `/login/dev`, que entra como o usuário `dev`.
2. No dashboard, cole a URL de um repositório público. A URL é validada na API do GitHub, o repositório é vinculado ao usuário e a coleta começa em background (o progresso é consultado em `/api/coleta/status`).
3. A página do repositório tem duas abas:
   - **GitHub**: commits por dia com seletor de período (1 dia, 7 dias, 3 meses ou 6 meses), linhas adicionadas e removidas, autores do período, linhas por autor, contribuidores e releases (API ao vivo).
   - **Análises FAP**: score de 0 a 100 com barras por componente e alertas explicáveis, evolução do score, comparação entre duas coletas do mesmo repositório, curva de concentração (top-1 e top-5 por mês) e métricas de sustentabilidade. O botão do relatório `.docx` fica no topo da página.
4. No dashboard, marque dois ou mais repositórios para compará-los em `/comparar`, ou abra o `/snapshot`, uma tabela consolidada por período coletado com exportação em CSV e XML.

## Requisitos

- Python 3.12 ou 3.13, com "Add to PATH". Versões mais novas podem falhar ao compilar dependências fixadas no `requirements.txt`.
- PostgreSQL na porta 5432.
- Token do GitHub. Na prática é obrigatório: sem ele o limite é de 60 requisições por hora, e o cálculo do TTFR faz uma requisição por issue, então a coleta não termina. Com token o limite é de 5.000 por hora.
- Login com GitHub: já vem com credenciais embutidas no `.env.example` — nada a configurar (criar um OAuth App próprio é opcional, ver seção "Login com GitHub").

## Instalação

### Automática

```bash
python setup.py
```

Veja o que o script faz em [Começando](#começando). Para rodar depois, dê duplo clique no `rodar.bat`; no terminal use `.\rodar.bat` (no PowerShell o `.\` é obrigatório, no CMD funciona direto).

### Manual

1. Crie o ambiente virtual e instale as dependências:

   ```bash
   python -m venv fap_env
   .\fap_env\Scripts\activate
   pip install -r requirements.txt
   ```

2. Crie o banco. O schema é aplicado sozinho quando o app sobe.

   ```bash
   psql -U postgres -d postgres -c "CREATE DATABASE fap;"
   ```

3. Copie o arquivo de exemplo e preencha as credenciais (`DB_PASSWORD` e `GITHUB_TOKEN`):

   ```bash
   copy .env.example .env
   ```

### Docker

Com o Docker Desktop instalado e o `.env` preenchido:

```bash
docker compose up --build
```

O compose sobe o app e o PostgreSQL na mesma rede, cria o banco e disponibiliza o app em http://localhost:5000. Os clones dos repositórios ficam no volume `dados-fap` e o banco em `postgres-dados`; ambos sobrevivem a um `docker compose down`.

## Configuração

### Variáveis do `.env`

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | sim | Acesso ao PostgreSQL (padrões: localhost, 5432, fap, postgres) |
| `GITHUB_TOKEN` | na prática, sim | Token pessoal do GitHub. Sem ele a coleta de TTFR não termina |
| `SESSION_SECRET` | sim | Segredo da sessão Flask e da cifra do token OAuth. Sem ele o token não é armazenado |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | não (já vêm preenchidas) | Credenciais do OAuth App do projeto, embutidas no `.env.example`. Só troque se quiser usar seu próprio OAuth App |
| `MESES_ANALISE` | não | Janela de análise em meses (padrão: 6) |
| `MINERACAO_INTERVALO_DIAS` | não | Intervalo da coleta automática em dias (padrão: 7) |
| `DB_CONNECT_TIMEOUT` | não | Timeout de conexão com o banco em segundos (padrão: 10) |
| `FAP_HOST`, `PORT` | não | Endereço e porta do servidor waitress (padrões: 127.0.0.1 e 5000) |
| `FAP_SEM_AUTOCOLETA` | não | Com `1`, nada é coletado no boot (usado pelos testes) |

### Login com GitHub (OAuth App)

O projeto já vem com um OAuth App cadastrado: o `GITHUB_CLIENT_ID` e o `GITHUB_CLIENT_SECRET` estão no `.env.example`, então quem clona o repositório recebe o botão "Entrar com GitHub" funcionando sem configurar nada (o `setup.py` copia o exemplo para o `.env`).

O callback registrado é `http://127.0.0.1:5000/callback`, que aponta para a máquina de cada pessoa — por isso o mesmo OAuth App serve todos os que baixarem o app localmente.

Para usar seu próprio OAuth App (opcional):

1. Em https://github.com/settings/developers, vá em **OAuth Apps** e clique em **New OAuth App**.
2. Preencha:
   - Homepage URL: `http://127.0.0.1:5000`
   - Authorization callback URL: `http://127.0.0.1:5000/callback`
3. Copie o Client ID, gere um Client Secret e substitua os dois no seu `.env`.
4. Reinicie o app.

Sem as credenciais no `.env`, o `/login` mostra "Entrar (modo desenvolvimento)", que entra como o usuário `dev`. Essa rota só existe em localhost e só enquanto o OAuth não estiver configurado.

Para publicar a FAP em um endereço público, acrescente `https://seu-endereco/callback` na lista de URIs de redirecionamento do OAuth App (cabe até 10) — o mesmo app atende localhost e produção.

## Executando

```bash
cd src
..\fap_env\Scripts\python.exe app.py
```

O venv fica na raiz do projeto. Se preferir, ative-o com `..\fap_env\Scripts\activate` e rode `python app.py`.

Acesse http://localhost:5000. No boot, os repositórios vinculados que ainda não foram coletados (`Repositorio.atualizado_em IS NULL`) são processados em background, e uma rotina do APScheduler (padrão: a cada 7 dias) mantém os dados atualizados.

### Coleta manual

Para rodar os coletores fora do app, com o banco já criado:

```bash
cd src
python -m collect.pydriller_collect    # commits, Bus Factor, churn relativo, autores por mês
python -m collect.github_metrics       # TTFR, issues, releases, contribuidores
```

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/repos` | Cadastra um repositório do usuário |
| PUT | `/repos/<id>` | Edita um repositório |
| DELETE | `/repos/<id>` | Apaga o repositório (em cascade com as métricas), se não sobrar outro dono |
| GET | `/api/repos` | Lista os repositórios em JSON (polling do dashboard) |
| GET | `/api/repo/<id>/resumo` | Série, autores, métricas, curva, histórico do score e score |
| GET | `/api/repo/<id>/github` | Contribuidores e releases (API ao vivo) |
| GET | `/api/repo/<id>/periodos` | Períodos coletados do repositório (base da comparação entre versões) |
| GET | `/api/coleta/status` | Estado da coleta |
| GET | `/comparar?ids=1,2` | Comparação lado a lado |
| GET | `/snapshot` | Tabela consolidada por período coletado |
| GET | `/snapshot.csv?periodo=AAAA-MM-DD\|AAAA-MM-DD` | Exporta o snapshot em CSV |
| GET | `/snapshot.xml?periodo=AAAA-MM-DD\|AAAA-MM-DD` | Exporta o snapshot em XML |
| GET | `/repo/<id>/relatorio` | Relatório `.docx` (score, métricas e os cinco gráficos) |
| POST | `/logout` | Encerra a sessão (só aceita POST) |
| GET | `/api/health` | Verificação de vida |

## Métricas

| Métrica | Definição |
|---------|-----------|
| Commits | Total de commits de autores humanos na janela de coleta |
| Bus Factor | Menor `k` tal que a soma das `k` maiores contribuições passa de 50% do total |
| TTFR | Mediana do tempo até a primeira resposta humana (exclui PRs e bots) |
| Churn relativo | (linhas adicionadas + removidas no período) / LOC do repositório |
| Cadência de releases | Releases publicados por mês na janela (`R / M`) |
| Contribuidores ativos | Pessoas distintas que abriram ou comentaram issues no período (sem bots) |
| Curva de concentração | Percentual dos commits do mês feitos pelo top-1 e pelo top-5 de autores |
| Score (0–100) | Média das componentes normalizadas, descritas abaixo |

O score é a média de quatro componentes: atividade (teto de 1000 commits), Bus Factor (teto de 5), responsividade (piso de 7 dias de TTFR) e estabilidade (piso de churn de 1,5). Métricas ausentes não entram na média.

Commits e Bus Factor consideram somente autores humanos. Contas automatizadas, como `dependabot[bot]`, são filtradas na coleta, seguindo a prática dos estudos de Truck Factor.

## Estrutura do projeto

```
fap/
├── requirements.txt
├── setup.py                    # instalação guiada (venv, .env, banco, schema)
├── rodar.bat                   # sobe o PostgreSQL e inicia o app (Windows)
├── Dockerfile                  # python:3.12-slim + git para o PyDriller
├── docker-compose.yml          # app + postgres:16 com volumes
├── .env.example                # copie para .env e preencha
├── conftest.py, pytest.ini     # raiz da suíte de testes
├── .github/workflows/lint.yml  # CI: pyflakes + pytest (PostgreSQL)
├── sql/schema.sql              # idempotente: métricas, usuários e vínculos
├── data/                       # clones dos repositórios minerados (gitignored)
├── src/
│   ├── config.py               # carrega as variáveis do .env
│   ├── database.py             # pool de conexões, ETL (upsert) e init_schema()
│   ├── status.py               # estado da coleta em background (thread-safe)
│   ├── analises.py             # curva de concentração e score de sustentabilidade
│   ├── relatorio.py            # relatório .docx (python-docx + matplotlib)
│   ├── app.py                  # Flask: OAuth, repos, APIs, snapshot e coleta
│   └── collect/
│       ├── pydriller_collect.py   # code churn, Bus Factor, churn relativo,
│       │                          # agregações por dia, mês e autor
│       └── github_metrics.py      # TTFR (mediana, sem bots), issues, releases
│                                  # e contribuidores (com retry e token)
├── templates/
│   ├── base.html               # topo (marca e usuário) e rodapé
│   ├── login.html              # cartão de autenticação
│   ├── meus_repos.html         # formulário e lista com status/polling
│   ├── repo_detalhe.html       # abas GitHub | Análises FAP e relatório
│   ├── comparar.html           # comparação lado a lado
│   └── snapshot.html           # snapshot consolidado por período
├── tests/                      # 104 testes: rotas, coletores, banco, score
└── static/
    ├── css/style.css           # tokens de design (IBM Plex, tema claro)
    └── js/
        ├── repos.js            # cadastro, edição, polling e seleção para comparar
        ├── comparar.js         # gráficos da comparação
        ├── repo_detalhe.js     # gráficos Plotly, score e abas
        └── plotly-2.35.2.min.js # Plotly local (funciona offline)
```

## Testes

```bash
pytest -q
```

Rode na raiz do projeto. Os testes precisam do PostgreSQL, e o CI executa os mesmos.

A suíte tem 112 testes: score e curva (matemática pura), utilitários dos coletores, rede mockada com `responses` (paginação, PRs, bots, rate limit, releases), helpers do banco (criptografia do token e upserts), autocoleta, relatório `.docx`, snapshot e smoke das rotas com login simulado.

## Licença

Distribuído sob a licença [MIT](LICENSE).