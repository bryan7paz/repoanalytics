"""Análises novas da FAP - contribuição além do que a API do GitHub expõe.

1. Curva de concentração de conhecimento: participação do top-5 de autores
   em cada mês (em % dos commits do mês).
2. Score de sustentabilidade (0–100): média dos componentes normalizados
   de atividade, Bus Factor, TTFR e churn relativo.
"""
import logging

from database import connection

log = logging.getLogger("fap.analises")

# Limites de normalização (documentados no artigo)
TETO_COMMITS = 1000    # 1000 commits/6 meses => atividade máxima
TETO_BUS_FACTOR = 5    # BF >= 5 => concentração máxima distribuída
PISO_TTFR_DIAS = 7     # TTFR >= 7 dias => score 0
PISO_CHURN = 1.5       # churn relativo >= 1,5 => score 0
TOP_AUTORES = 5

# Limiar + mensagem para o alerta explicável de cada componente do score
ALERTAS = {
    "Atividade": (30, "Poucos commits na janela - o projeto pode estar estagnado."),
    "Bus Factor": (60, "Conhecimento concentrado em 1-2 pessoas - risco de continuidade do projeto."),
    "Responsividade": (50, "Tempo de resposta alto - contribuidores novos podem desistir de participar."),
    "Estabilidade": (40, "Volume alto de código reescrito - pode indicar retrabalho na base de código."),
}


def _limitar(valor):
    """Mantém o componente dentro de [0, 100] mesmo com dados fora do esperado."""
    return max(0.0, min(100.0, valor))


def curva_concentracao(id_repositorio, mes_inicio=None):
    """Participação do top-5 de autores por mês (% dos commits do mês).

    mes_inicio (opcional): restringe a série à janela de análise.
    Retorna lista ordenada: [{"mes": "2026-04", "top5": 62.5, "top1": 40.0,
                              "autores": 12, "commits": 180}, ...]
    """
    with connection() as conn:
        import pandas as pd
        where = "WHERE id_repositorio = %s"
        params = (id_repositorio,)
        if mes_inicio is not None:
            where += " AND mes >= %s"
            params = (id_repositorio, mes_inicio)
        df = pd.read_sql(
            f"""
            SELECT mes, autor, commits FROM Metrica_Autor_Mensal
            {where} ORDER BY mes
            """,
            conn,
            params=params,
        )
    if df.empty:
        return []
    serie = []
    for mes, g in df.groupby("mes"):
        total = int(g["commits"].sum())
        if not total:
            continue
        ordenado = g.sort_values("commits", ascending=False)
        top5 = int(ordenado["commits"].head(TOP_AUTORES).sum())
        top1 = int(ordenado["commits"].iloc[0])
        periodo = pd.Period(mes, freq="M") if not isinstance(mes, pd.Period) else mes
        serie.append(
            {
                "mes": str(periodo),
                "top5": round(top5 / total * 100, 1),
                "top1": round(top1 / total * 100, 1),
                "autores": int(len(g)),
                "commits": total,
            }
        )
    serie.sort(key=lambda x: x["mes"])
    return serie


def score_sustentabilidade(metricas):
    """Score composto 0–100 a partir das métricas normalizadas.

    metricas: dict com commits, bus_factor, ttfr, churn_relativo
              (valores None são excluídos da média).
    Cada componente abaixo do limiar carrega um "alerta" explicável.
    """

    def componente(nome, descricao, valor):
        c = {"nome": nome, "descricao": descricao, "valor": valor}
        limiar, msg = ALERTAS[nome]
        if valor < limiar:
            c["alerta"] = msg
        return c

    componentes = []

    commits = metricas.get("commits")
    if commits is not None:
        componentes.append(componente(
            "Atividade", f"{commits} commits (teto {TETO_COMMITS})",
            round(_limitar(commits / TETO_COMMITS * 100), 1)))

    bf = metricas.get("bus_factor")
    if bf is not None:
        componentes.append(componente(
            "Bus Factor", f"BF = {bf} (teto {TETO_BUS_FACTOR})",
            round(_limitar(bf / TETO_BUS_FACTOR * 100), 1)))

    ttfr = metricas.get("ttfr")
    if ttfr is not None:
        componentes.append(componente(
            "Responsividade", f"TTFR = {ttfr:.2f} dia (piso {PISO_TTFR_DIAS}d)",
            round(_limitar((1 - ttfr / PISO_TTFR_DIAS) * 100), 1)))

    churn = metricas.get("churn_relativo")
    if churn is not None:
        componentes.append(componente(
            "Estabilidade", f"churn = {churn:.3f} (piso {PISO_CHURN})",
            round(_limitar((1 - churn / PISO_CHURN) * 100), 1)))

    if not componentes:
        return {"score": None, "componentes": []}
    score = round(sum(c["valor"] for c in componentes) / len(componentes), 1)
    return {"score": score, "componentes": componentes}
