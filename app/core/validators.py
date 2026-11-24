"""Validação de documentos e códigos usados no cadastro (CNPJ, CPF, UF, município IBGE, CNAE)."""

import re

from django.core.exceptions import ValidationError

# Código IBGE de cada UF (dois primeiros dígitos do código de município).
UFS = {
    "RO": "11",
    "AC": "12",
    "AM": "13",
    "RR": "14",
    "PA": "15",
    "AP": "16",
    "TO": "17",
    "MA": "21",
    "PI": "22",
    "CE": "23",
    "RN": "24",
    "PB": "25",
    "PE": "26",
    "AL": "27",
    "SE": "28",
    "BA": "29",
    "MG": "31",
    "ES": "32",
    "RJ": "33",
    "SP": "35",
    "PR": "41",
    "SC": "42",
    "RS": "43",
    "MS": "50",
    "MT": "51",
    "GO": "52",
    "DF": "53",
}
UF_CHOICES = [(uf, uf) for uf in sorted(UFS)]

_PESOS_CNPJ_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_PESOS_CNPJ_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def limpar_documento(valor):
    """Remove máscara e põe em maiúsculas (CNPJ alfanumérico)."""
    return re.sub(r"[^0-9A-Za-z]", "", valor or "").upper()


def _dv_mod11(valores, pesos):
    resto = sum(v * p for v, p in zip(valores, pesos, strict=True)) % 11
    return 0 if resto < 2 else 11 - resto


def cnpj_valido(valor):
    """CNPJ numérico ou alfanumérico (IN RFB 2.229/2024, em vigor desde julho de 2026).

    As 12 primeiras posições aceitam 0-9 e A-Z; os 2 dígitos verificadores são sempre numéricos.
    Cada caractere vale ``ord(c) - 48`` no cálculo do módulo 11, o que mantém o CNPJ numérico igual.
    """
    cnpj = limpar_documento(valor)
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", cnpj):
        return False
    if len(set(cnpj)) == 1:
        return False
    valores = [ord(c) - 48 for c in cnpj]
    dv1 = _dv_mod11(valores[:12], _PESOS_CNPJ_1)
    dv2 = _dv_mod11(valores[:12] + [dv1], _PESOS_CNPJ_2)
    return valores[12] == dv1 and valores[13] == dv2


def cpf_valido(valor):
    cpf = limpar_documento(valor)
    if not re.fullmatch(r"[0-9]{11}", cpf) or len(set(cpf)) == 1:
        return False
    numeros = [int(c) for c in cpf]
    for posicao in (9, 10):
        soma = sum(n * p for n, p in zip(numeros[:posicao], range(posicao + 1, 1, -1), strict=True))
        dv = (soma * 10) % 11 % 10
        if numeros[posicao] != dv:
            return False
    return True


def validar_cnpj(valor):
    if not cnpj_valido(valor):
        raise ValidationError("CNPJ inválido.", code="cnpj_invalido")


def validar_cpf_ou_cnpj(valor):
    doc = limpar_documento(valor)
    if len(doc) == 11 and cpf_valido(doc):
        return
    if len(doc) == 14 and cnpj_valido(doc):
        return
    raise ValidationError("Informe um CPF ou CNPJ válido.", code="documento_invalido")


def validar_cnae(valor):
    if not re.fullmatch(r"[0-9]{7}", valor or ""):
        raise ValidationError("CNAE deve ter 7 dígitos (subclasse).", code="cnae_invalido")


def validar_municipio_da_uf(municipio, uf):
    """Município IBGE com 7 dígitos e prefixo da UF.

    A lista oficial de municípios vem dos dados abertos do motor e entra na Fase 2; aqui garantimos o
    formato e a coerência com a UF.
    """
    if not re.fullmatch(r"[0-9]{7}", municipio or ""):
        raise ValidationError({"municipio_ibge": "Código IBGE do município deve ter 7 dígitos."})
    if uf in UFS and not municipio.startswith(UFS[uf]):
        raise ValidationError({"municipio_ibge": f"Município não pertence à UF {uf}."})
