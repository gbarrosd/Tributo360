"""Contratos internos entre o Tributo360 e os motores da Receita (planejamento, seção 3).

O resto do código só conhece estes objetos. O JSON de cada motor fica dentro dos clientes
(`engines/clientes/`): quando um motor mudar de formato, muda só o cliente e os casos dourados.

Valores monetários e alíquotas são sempre `Decimal`. Para os motores eles vão como texto ("100.00"),
que os dois motores Java aceitam e que evita qualquer arredondamento por ponto flutuante.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

# --- Comuns -----------------------------------------------------------------------------------


class Motor(StrEnum):
    REGIME_REGULAR = "regime_regular"
    SIMPLES = "simples"
    DANFSE = "danfse"


class EstadoItem(StrEnum):
    """`estadoItem` devolvido pelos endpoints de observabilidade."""

    CALCULADO = "CALCULADO"
    CALCULO_SIMULADO = "CALCULO_SIMULADO"
    INCONSISTENCIA_ENTRADA = "INCONSISTENCIA_ENTRADA"
    NAO_IMPLEMENTADO = "NAO_IMPLEMENTADO"
    FALHA_TECNICA = "FALHA_TECNICA"
    DESCONHECIDO = "DESCONHECIDO"  # estado novo que o motor passou a devolver e ainda não mapeamos

    @classmethod
    def de(cls, valor):
        try:
            return cls(valor)
        except ValueError:
            return cls.DESCONHECIDO

    @property
    def calculou(self):
        return self in {EstadoItem.CALCULADO, EstadoItem.CALCULO_SIMULADO}


@dataclass(frozen=True)
class VersaoInfo:
    """Versão do motor e da base de tabelas usada num cálculo."""

    motor: Motor
    versao_app: str
    versao_db: str = ""
    data_db: date | None = None
    descricao_db: str = ""
    ambiente: str = ""


@dataclass(frozen=True)
class ErroMotor:
    """Erro no formato RFC 7807 dos motores, já traduzido para nomes nossos."""

    codigo: str
    titulo: str
    detalhe: str
    campo: str = ""  # JSON pointer do motor, ex.: "#/itens/3/ncm"

    def como_dict(self):
        return {"codigo": self.codigo, "titulo": self.titulo, "detalhe": self.detalhe, "campo": self.campo}


@dataclass(frozen=True)
class ValoresItem:
    """Valores principais de um item calculado. Ausente = o motor não devolveu o grupo."""

    base_calculo: Decimal | None = None
    cbs: Decimal | None = None
    ibs: Decimal | None = None
    ibs_uf: Decimal | None = None
    ibs_mun: Decimal | None = None
    imposto_seletivo: Decimal | None = None
    aliquota_cbs: Decimal | None = None
    aliquota_ibs_uf: Decimal | None = None
    aliquota_ibs_mun: Decimal | None = None
    aliquota_ibs: Decimal | None = None  # Simples: pIBSSN (não separa UF/município)

    def como_dict(self):
        return {chave: (None if valor is None else str(valor)) for chave, valor in self.__dict__.items()}


@dataclass(frozen=True)
class ResultadoItem:
    numero: int
    estado: EstadoItem
    valores: ValoresItem = field(default_factory=ValoresItem)
    erros: tuple[ErroMotor, ...] = ()
    memoria: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)  # ex.: resolução da alíquota do Simples


@dataclass(frozen=True)
class Totais:
    """Totais da operação. Os motores somam só os itens calculados."""

    base_calculo: Decimal = Decimal("0")
    cbs: Decimal = Decimal("0")
    ibs: Decimal = Decimal("0")
    imposto_seletivo: Decimal = Decimal("0")

    @property
    def tributos(self):
        return self.cbs + self.ibs + self.imposto_seletivo

    def como_dict(self):
        return {
            "base_calculo": str(self.base_calculo),
            "cbs": str(self.cbs),
            "ibs": str(self.ibs),
            "imposto_seletivo": str(self.imposto_seletivo),
            "tributos": str(self.tributos),
        }


@dataclass(frozen=True)
class ResultadoCalculo:
    motor: Motor
    versao: VersaoInfo
    simulado: bool  # header x-warning-dados-simulados ou algum item CALCULO_SIMULADO
    itens: tuple[ResultadoItem, ...]
    totais: Totais
    payload_enviado: dict
    resposta_bruta: dict
    aviso_simulado: str = ""  # código do header x-warning-dados-simulados, quando houver
    extras: dict = field(default_factory=dict)
    duracao_ms: int = 0

    @property
    def todos_calcularam(self):
        return all(item.estado.calculou for item in self.itens)


# --- Regime regular -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AliquotasNominais:
    """Alíquotas nominais (%) de CBS e IBS. Obrigatórias a partir de 2027, proibidas antes."""

    cbs: Decimal
    ibs_estadual: Decimal
    ibs_municipal: Decimal
    is_ad_valorem: Decimal | None = None
    is_ad_rem: Decimal | None = None


@dataclass(frozen=True)
class ImpostoSeletivo:
    cst: str
    c_class_trib: str
    base_calculo: Decimal
    quantidade: Decimal | None = None
    unidade: str = ""
    imposto_informado: Decimal | None = None


@dataclass(frozen=True)
class ItemRegular:
    numero: int
    cst: str
    c_class_trib: str
    base_calculo: Decimal | None = None
    ncm: str = ""
    nbs: str = ""
    quantidade: Decimal | None = None
    unidade: str = ""
    aliquotas: AliquotasNominais | None = None
    imposto_seletivo: ImpostoSeletivo | None = None


@dataclass(frozen=True)
class OperacaoRegular:
    fato_gerador: datetime
    uf: str
    municipio: int
    itens: tuple[ItemRegular, ...]
    id: str = ""
    tp_doc: int | None = None


# --- Simples Nacional ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParAliquotaSN:
    """Par de alíquotas efetivas (%) de CBS e IBS de um anexo, como no PGDAS-D."""

    cbs: Decimal
    ibs: Decimal


@dataclass(frozen=True)
class AliquotaAnexoSN:
    par: ParAliquotaSN
    # Anexos III a V: par sem a redistribuição do ISS (isenção/imunidade municipal etc.).
    sem_redistribuicao_iss: ParAliquotaSN | None = None


ANEXOS_SN = ("I", "II", "III", "IV", "V")


@dataclass(frozen=True)
class AliquotasEfetivasSN:
    fator_r: bool
    impedimento_sublimite: bool
    anexos: dict[str, AliquotaAnexoSN]  # chave: "I" a "V"


@dataclass(frozen=True)
class ItemSimples:
    numero: int
    c_ativ_sn: str
    c_class_trib: str
    base_calculo: Decimal
    ncm: str = ""
    nbs: str = ""
    valor_imposto_seletivo: Decimal | None = None
    # Campos da NFS-e que influenciam a escolha do par de alíquotas (tribISSQN, tpRetISSQN, tpBM).
    nfse: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OperacaoSimples:
    data: date
    tp_doc: str
    aliquotas_efetivas: AliquotasEfetivasSN
    itens: tuple[ItemSimples, ...]
    # Regime no vocabulário do documento: CRT (NF-e/NFC-e...) ou os campos da NFS-e.
    crt: str = ""
    op_simp_nac: str = ""
    reg_ap_ibscbs_sn: str = ""
    reg_ap_trib_sn: str = ""
    id: str = ""


# --- Validação de XML ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoValidacaoXml:
    valido: bool
    mensagem: str = ""
    linha: int | None = None
    coluna: int | None = None
