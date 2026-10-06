"""Testes do módulo analises - matemática pura do score + curva sem dados."""
from analises import (PISO_CHURN, PISO_TTFR_DIAS, TETO_BUS_FACTOR,
                      TETO_COMMITS, curva_concentracao, score_sustentabilidade)


def test_score_sem_métricas_retorna_none():
    assert score_sustentabilidade({}) == {"score": None, "componentes": []}


def test_score_todos_none_excluidos_da_média():
    r = score_sustentabilidade({"commits": None, "bus_factor": None,
                                "ttfr": None, "churn_relativo": None})
    assert r == {"score": None, "componentes": []}


def test_score_nos_tetos_e_pisos_zerados():
    r = score_sustentabilidade({"commits": TETO_COMMITS,
                                "bus_factor": TETO_BUS_FACTOR,
                                "ttfr": 0.0, "churn_relativo": 0.0})
    assert r["score"] == 100.0
    assert len(r["componentes"]) == 4


def test_score_metade_do_caminho():
    r = score_sustentabilidade({"commits": TETO_COMMITS / 2,
                                "bus_factor": TETO_BUS_FACTOR / 2})
    assert r["score"] == 50.0


def test_score_atividade_com_teto_em_100():
    r = score_sustentabilidade({"commits": TETO_COMMITS * 3})
    assert r["componentes"][0]["valor"] == 100.0


def test_score_pisos_zeram_responsividade_e_estabilidade():
    r = score_sustentabilidade({"ttfr": PISO_TTFR_DIAS + 1,
                                "churn_relativo": PISO_CHURN + 1})
    vals = {c["nome"]: c["valor"] for c in r["componentes"]}
    assert vals["Responsividade"] == 0.0
    assert vals["Estabilidade"] == 0.0


def test_score_componentes_nunca_ultrapassam_100():
    r = score_sustentabilidade({"commits": 10**6, "bus_factor": 99,
                                "ttfr": -5.0, "churn_relativo": -1.0})
    assert all(0.0 <= c["valor"] <= 100.0 for c in r["componentes"])


def test_score_média_de_componentes_com_Nones():
    r = score_sustentabilidade({"commits": TETO_COMMITS, "bus_factor": None,
                                "ttfr": 0.0, "churn_relativo": None})
    assert r["score"] == 100.0  # (100 + 100) / 2
    assert len(r["componentes"]) == 2


def test_score_resultado_com_um_casual():
    r = score_sustentabilidade({"commits": 539, "bus_factor": 5,
                                "ttfr": None, "churn_relativo": 0.120})
    # 53.9 + 100 + 92.0 -> 245.9 / 3 = 81.97 -> 82.0
    assert r["score"] == 82.0


def test_componente_ruim_carrega_alerta():
    r = score_sustentabilidade({"commits": 10, "bus_factor": 1,
                                "ttfr": 6.0, "churn_relativo": 1.4})
    alertas = {c["nome"]: c.get("alerta") for c in r["componentes"]}
    assert alertas["Atividade"]         # 10 commits << limiar
    assert alertas["Bus Factor"]        # BF 1 -> 20 < 60
    assert alertas["Responsividade"]    # TTFR 6d -> 14.3 < 50
    assert alertas["Estabilidade"]      # churn 1.4 -> 6.7 < 40


def test_componente_bom_sem_alerta():
    r = score_sustentabilidade({"commits": 2000, "bus_factor": 5,
                                "ttfr": 0.0, "churn_relativo": 0.0})
    assert all("alerta" not in c for c in r["componentes"])


def test_curva_repo_inexistente_retorna_lista_vazia():
    assert curva_concentracao(10**9) == []
