"""Leitura do XML da NFS-e nacional (leiaute 1.01) com o grupo IBSCBS da Reforma Tributária.

A NFS-e tem um único serviço. A classificação (CST, cClassTrib) e o NBS vêm da DPS, e os valores
apurados (base, alíquotas, CBS e IBS) vêm do grupo IBSCBS da própria NFS-e (`infNFSe/IBSCBS`).
O regime do prestador vem de `prest/regTrib/opSimpNac`: 1 não optante, 2 MEI, 3 ME/EPP.
"""

import hashlib
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import tostring

from .nfe import UF_POR_CODIGO, ItemNota, NotaLida, XmlInvalido, _data

NS = "{http://www.sped.fazenda.gov.br/nfse}"
TIPO_XSD = "nfse"
NOME = "NFS-e"
MODELO = "nfse"

# opSimpNac → CRT equivalente ao da NF-e, para a conferência tratar os dois documentos igual.
CRT_POR_OP_SIMP_NAC = {"1": "3", "2": "4", "3": "1"}


def _achar(no, caminho):
    return no.find(caminho.replace("n:", NS)) if no is not None else None


def _texto(no, caminho):
    achado = _achar(no, caminho)
    return (achado.text or "").strip() if achado is not None else ""


def _decimal(no, caminho):
    texto = _texto(no, caminho)
    if not texto:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise XmlInvalido(f"Valor numérico inválido em {caminho.replace('n:', '')}: {texto!r}") from None


def _nota(nfse):
    inf = _achar(nfse, "n:infNFSe")
    if inf is None:
        raise XmlInvalido("NFS-e sem o grupo infNFSe.")
    dps = _achar(inf, "n:DPS/n:infDPS")
    if dps is None:
        raise XmlInvalido("NFS-e sem a DPS (infNFSe/DPS/infDPS).")
    emit, prest, toma = _achar(inf, "n:emit"), _achar(dps, "n:prest"), _achar(dps, "n:toma")
    servico = _achar(dps, "n:serv/n:cServ")
    apurado = _achar(inf, "n:IBSCBS")
    classificacao = _achar(dps, "n:IBSCBS/n:valores/n:trib/n:gIBSCBS")
    municipio = _texto(apurado, "n:cLocalidadeIncid") or _texto(inf, "n:cLocIncid")

    item = ItemNota(
        numero=1,
        codigo=_texto(servico, "n:cTribNac"),
        descricao=_texto(servico, "n:xDescServ") or _texto(inf, "n:xTribNac"),
        ncm="",
        cfop="",
        quantidade=None,
        unidade="",
        valor_produto=_decimal(dps, "n:valores/n:vServPrest/n:vServ"),
        cst=_texto(classificacao, "n:CST"),
        c_class_trib=_texto(classificacao, "n:cClassTrib"),
        nbs=_texto(servico, "n:cNBS"),
    )
    if apurado is not None:
        valores, totais = _achar(apurado, "n:valores"), _achar(apurado, "n:totCIBS")
        item.base_calculo = _decimal(valores, "n:vBC")
        item.aliquota_ibs_uf = _decimal(valores, "n:uf/n:pIBSUF")
        item.aliquota_ibs_mun = _decimal(valores, "n:mun/n:pIBSMun")
        item.aliquota_cbs = _decimal(valores, "n:fed/n:pCBS")
        item.ibs_uf = _decimal(totais, "n:gIBS/n:gIBSUFTot/n:vIBSUF")
        item.ibs_mun = _decimal(totais, "n:gIBS/n:gIBSMunTot/n:vIBSMun")
        item.ibs = _decimal(totais, "n:gIBS/n:vIBSTot")
        item.cbs = _decimal(totais, "n:gCBS/n:vCBS")

    totais = {
        "base_calculo": item.base_calculo,
        "cbs": item.cbs,
        "ibs": item.ibs,
        "valor_nota": _decimal(_achar(apurado, "n:totCIBS"), "n:vTotNF") or item.valor_produto,
    }
    return NotaLida(
        chave=(inf.get("Id") or "").removeprefix("NFS"),
        modelo=MODELO,
        numero=_texto(inf, "n:nNFSe"),
        serie=_texto(dps, "n:serie"),
        emissao=_data(_texto(dps, "n:dhEmi")),
        uf=UF_POR_CODIGO.get(municipio[:2], ""),
        municipio=municipio,
        emitente_cnpj=_texto(emit, "n:CNPJ") or _texto(emit, "n:CPF"),
        emitente_nome=_texto(emit, "n:xNome"),
        emitente_crt=CRT_POR_OP_SIMP_NAC.get(_texto(prest, "n:regTrib/n:opSimpNac"), ""),
        destinatario_cnpj=_texto(toma, "n:CNPJ") or _texto(toma, "n:CPF"),
        destinatario_nome=_texto(toma, "n:xNome"),
        itens=[item],
        totais={chave: str(valor) for chave, valor in totais.items() if valor is not None},
        hash=hashlib.sha256(tostring(nfse)).hexdigest(),
    )


def eh_nfse(raiz):
    return raiz.tag.startswith(NS)


def notas(raiz) -> list[NotaLida]:
    """Notas sob a raiz: a própria NFSe ou qualquer elemento que a contenha."""
    encontradas = [raiz] if raiz.tag == f"{NS}NFSe" else raiz.findall(f".//{NS}NFSe")
    if not encontradas:
        raise XmlInvalido("O arquivo está no namespace da NFS-e nacional, mas não contém uma NFS-e (NFSe).")
    return [_nota(nfse) for nfse in encontradas]
