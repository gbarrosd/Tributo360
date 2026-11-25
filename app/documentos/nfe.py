"""Leitura do XML da NF-e (modelo 55) com o grupo IBSCBS da Reforma Tributária (NT 2025.002).

Só extrai o que a conferência usa: identificação, emitente/destinatário, local e data do fato gerador e,
por item, a classificação (CST, cClassTrib, NCM) e os valores destacados de CBS, IBS e Imposto
Seletivo. O XML vem do cliente, então é lido com defusedxml (sem DTD nem entidades externas).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError, tostring

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

NS = "{http://www.portalfiscal.inf.br/nfe}"
TIPO_XSD = "nfe"
NOME = "NF-e"

# Código IBGE da UF (dois primeiros dígitos do município) → sigla.
UF_POR_CODIGO = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
    "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS",
    "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}  # fmt: skip


CAMPOS_DECIMAIS = {
    "quantidade", "valor_produto", "base_calculo", "aliquota_cbs", "aliquota_ibs_uf", "aliquota_ibs_mun",
    "cbs", "ibs_uf", "ibs_mun", "ibs", "imposto_seletivo",
}  # fmt: skip


class XmlInvalido(Exception):
    """XML malformado ou que não é uma NF-e modelo 55. `linha`/`coluna` quando o leitor sabe onde."""

    def __init__(self, mensagem, linha=None, coluna=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.linha = linha
        self.coluna = coluna


@dataclass
class ItemNota:
    numero: int
    codigo: str
    descricao: str
    ncm: str
    cfop: str
    quantidade: Decimal | None
    unidade: str
    valor_produto: Decimal | None
    cst: str = ""
    c_class_trib: str = ""
    # Valores destacados na nota (None = a nota não traz o campo)
    base_calculo: Decimal | None = None
    aliquota_cbs: Decimal | None = None
    aliquota_ibs_uf: Decimal | None = None
    aliquota_ibs_mun: Decimal | None = None
    cbs: Decimal | None = None
    ibs_uf: Decimal | None = None
    ibs_mun: Decimal | None = None
    ibs: Decimal | None = None
    imposto_seletivo: Decimal | None = None
    seletivo: dict = field(default_factory=dict)  # CSTIS, cClassTribIS, vBCIS, pIS, qTrib, uTrib
    nbs: str = ""  # só na NFS-e

    @property
    def tem_ibscbs(self):
        return bool(self.cst and self.c_class_trib)

    def como_dict(self):
        return {chave: (str(valor) if isinstance(valor, Decimal) else valor) for chave, valor in self.__dict__.items()}


@dataclass
class NotaLida:
    chave: str
    modelo: str
    numero: str
    serie: str
    emissao: datetime
    uf: str
    municipio: str
    emitente_cnpj: str
    emitente_nome: str
    emitente_crt: str
    destinatario_cnpj: str
    destinatario_nome: str
    itens: list[ItemNota]
    totais: dict
    hash: str


def _texto(no, caminho):
    if no is None:
        return ""
    achado = no.find(caminho.replace("n:", NS))
    return (achado.text or "").strip() if achado is not None else ""


def _decimal(no, caminho):
    texto = _texto(no, caminho)
    if not texto:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise XmlInvalido(f"Valor numérico inválido em {caminho.replace('n:', '')}: {texto!r}") from None


def _achar(no, caminho):
    return no.find(caminho.replace("n:", NS)) if no is not None else None


def _data(texto):
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        raise XmlInvalido(f"Data de emissão inválida: {texto!r}") from None


def _item(det):
    prod = _achar(det, "n:prod")
    imposto = _achar(det, "n:imposto")
    item = ItemNota(
        numero=int(det.get("nItem") or 0),
        codigo=_texto(prod, "n:cProd"),
        descricao=_texto(prod, "n:xProd"),
        ncm=_texto(prod, "n:NCM"),
        cfop=_texto(prod, "n:CFOP"),
        quantidade=_decimal(prod, "n:qTrib") or _decimal(prod, "n:qCom"),
        unidade=_texto(prod, "n:uTrib") or _texto(prod, "n:uCom"),
        valor_produto=_decimal(prod, "n:vProd"),
    )
    ibscbs = _achar(imposto, "n:IBSCBS")
    if ibscbs is not None:
        item.cst = _texto(ibscbs, "n:CST")
        item.c_class_trib = _texto(ibscbs, "n:cClassTrib")
        grupo = _achar(ibscbs, "n:gIBSCBS")
        if grupo is not None:
            item.base_calculo = _decimal(grupo, "n:vBC")
            item.aliquota_ibs_uf = _decimal(grupo, "n:gIBSUF/n:pIBSUF")
            item.ibs_uf = _decimal(grupo, "n:gIBSUF/n:vIBSUF")
            item.aliquota_ibs_mun = _decimal(grupo, "n:gIBSMun/n:pIBSMun")
            item.ibs_mun = _decimal(grupo, "n:gIBSMun/n:vIBSMun")
            item.ibs = _decimal(grupo, "n:vIBS")
            item.aliquota_cbs = _decimal(grupo, "n:gCBS/n:pCBS")
            item.cbs = _decimal(grupo, "n:gCBS/n:vCBS")
    seletivo = _achar(imposto, "n:IS")
    if seletivo is not None:
        item.imposto_seletivo = _decimal(seletivo, "n:vIS")
        item.seletivo = {
            "cst": _texto(seletivo, "n:CSTIS"),
            "c_class_trib": _texto(seletivo, "n:cClassTribIS"),
            "base_calculo": _texto(seletivo, "n:vBCIS"),
            "aliquota": _texto(seletivo, "n:pIS"),
            "quantidade": _texto(seletivo, "n:qTrib"),
            "unidade": _texto(seletivo, "n:uTrib"),
        }
    return item


def _nota(nfe):
    inf = _achar(nfe, "n:infNFe")
    if inf is None:
        raise XmlInvalido("NF-e sem o grupo infNFe.")
    ide = _achar(inf, "n:ide")
    modelo = _texto(ide, "n:mod")
    if modelo != "55":
        raise XmlInvalido(f"Documento modelo {modelo or '?'}: por enquanto o Tributo360 confere só NF-e modelo 55.")
    emit, dest = _achar(inf, "n:emit"), _achar(inf, "n:dest")
    municipio = _texto(ide, "n:cMunFG")
    itens = [_item(det) for det in inf.findall(f"{NS}det")]
    if not itens:
        raise XmlInvalido("NF-e sem itens (grupo det).")
    tot = _achar(inf, "n:total/n:IBSCBSTot")
    totais = {
        "base_calculo": _decimal(tot, "n:vBCIBSCBS"),
        "cbs": _decimal(tot, "n:gCBS/n:vCBS"),
        "ibs": _decimal(tot, "n:gIBS/n:vIBS"),
        "imposto_seletivo": _decimal(_achar(inf, "n:total/n:ISTot"), "n:vIS"),
        "valor_nota": _decimal(inf, "n:total/n:ICMSTot/n:vNF"),
    }
    return NotaLida(
        chave=(inf.get("Id") or "").removeprefix("NFe"),
        modelo=modelo,
        numero=_texto(ide, "n:nNF"),
        serie=_texto(ide, "n:serie"),
        emissao=_data(_texto(ide, "n:dhEmi")),
        uf=UF_POR_CODIGO.get(municipio[:2], ""),
        municipio=municipio,
        emitente_cnpj=_texto(emit, "n:CNPJ") or _texto(emit, "n:CPF"),
        emitente_nome=_texto(emit, "n:xNome"),
        emitente_crt=_texto(emit, "n:CRT"),
        destinatario_cnpj=_texto(dest, "n:CNPJ") or _texto(dest, "n:CPF"),
        destinatario_nome=_texto(dest, "n:xNome"),
        itens=itens,
        totais={chave: str(valor) for chave, valor in totais.items() if valor is not None},
        hash=hashlib.sha256(tostring(nfe)).hexdigest(),
    )


def analisar(conteudo: bytes):
    """Elemento raiz do XML, lido com segurança. Erros de sintaxe trazem linha e coluna."""
    try:
        return fromstring(conteudo, forbid_dtd=True)
    except ParseError as erro:
        linha, coluna = getattr(erro, "position", (None, None))
        raise XmlInvalido(f"XML malformado: {erro}", linha, (coluna + 1) if coluna is not None else None) from None
    except DefusedXmlException:
        raise XmlInvalido("XML com DTD ou entidades não é aceito.") from None


def notas(raiz) -> list[NotaLida]:
    """Notas sob a raiz: aceita NFe, nfeProc (nota autorizada) e enviNFe (lote com uma ou mais NFe)."""
    nfes = [raiz] if raiz.tag == f"{NS}NFe" else raiz.findall(f".//{NS}NFe")
    if not nfes:
        raise XmlInvalido(
            "O arquivo não contém uma NF-e nem uma NFS-e nacional "
            "(esperado NFe, nfeProc ou enviNFe, ou NFSe no namespace da NFS-e nacional)."
        )
    return [_nota(nfe) for nfe in nfes]


def ler(conteudo: bytes) -> list[NotaLida]:
    return notas(analisar(conteudo))
