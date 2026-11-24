"""Alíquotas nominais de IBS/CBS para uma operação (decisão D2).

A partir de 2027 o motor do regime regular exige as alíquotas na entrada. O Tributo360 propõe as da
sua tabela (`AliquotaNominal`, sempre com fonte) e o contador pode editar cada uma. A origem de cada
valor fica registrada no item da simulação:

- ``motor_2026``: antes de 2027 o motor usa as alíquotas de teste dele e não aceita outras;
- ``tabela_tributo360``: veio da nossa tabela (guarda o id e a fonte do registro);
- ``editada_pelo_usuario``: o contador digitou (guarda o valor que a tabela sugeria, se havia).

O motor devolve `CALCULO_SIMULADO` nos dois últimos casos; quem distingue a origem somos nós.
"""

from dataclasses import dataclass
from datetime import date

from .models import AliquotaNominal

ANO_ALIQUOTAS_INFORMADAS = 2027

CAMPOS = {
    # campo do contrato → esfera da tabela
    "cbs": AliquotaNominal.Esfera.UNIAO,
    "ibs_estadual": AliquotaNominal.Esfera.UF,
    "ibs_municipal": AliquotaNominal.Esfera.MUNICIPIO,
}

ORIGEM_MOTOR_2026 = "motor_2026"
ORIGEM_TABELA = "tabela_tributo360"
ORIGEM_USUARIO = "editada_pelo_usuario"


def exige_aliquotas(data: date):
    return data.year >= ANO_ALIQUOTAS_INFORMADAS


@dataclass(frozen=True)
class AliquotaProposta:
    campo: str
    valor: object  # Decimal | None
    registro: AliquotaNominal | None

    def como_origem(self):
        if self.registro is None:
            return None
        return {
            "origem": ORIGEM_TABELA,
            "valor": str(self.valor),
            "aliquota_nominal_id": self.registro.pk,
            "fonte": self.registro.fonte,
            "data_publicacao": self.registro.data_publicacao.isoformat(),
        }


def propostas_da_tabela(data: date, municipio_ibge: str):
    """Alíquotas vigentes na nossa tabela para a data e o município. Campo sem cadastro vem com `None`."""
    municipio_ibge = str(municipio_ibge)
    codigos = {"cbs": "", "ibs_estadual": municipio_ibge[:2], "ibs_municipal": municipio_ibge}
    propostas = {}
    for campo, esfera in CAMPOS.items():
        registro = AliquotaNominal.vigente(esfera, data, codigos[campo])
        propostas[campo] = AliquotaProposta(campo, registro.aliquota if registro else None, registro)
    return propostas


def resolver(propostas, informadas):
    """Combina as propostas da tabela com o que o usuário informou.

    `informadas` é um dict parcial {campo: Decimal}. Devolve `(valores, origens, faltando)`.
    """
    valores, origens, faltando = {}, {}, []
    for campo in CAMPOS:
        proposta = propostas[campo]
        if campo in informadas and informadas[campo] is not None:
            valor = informadas[campo]
            valores[campo] = valor
            if proposta.registro is not None and proposta.valor == valor:
                origens[campo] = proposta.como_origem()
            else:
                origens[campo] = {
                    "origem": ORIGEM_USUARIO,
                    "valor": str(valor),
                    "valor_anterior": None if proposta.valor is None else str(proposta.valor),
                    "aliquota_nominal_id": proposta.registro.pk if proposta.registro else None,
                }
        elif proposta.registro is not None:
            valores[campo] = proposta.valor
            origens[campo] = proposta.como_origem()
        else:
            faltando.append(campo)
    return valores, origens, faltando
