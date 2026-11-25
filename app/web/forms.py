"""Formulários das telas. Servem para desenhar a tela e devolver o que foi digitado.

A validação de verdade é a mesma da API: as views passam os dados para os serializers do DRF e para
os serviços, e os erros voltam para os campos com `aplicar_erros`. Assim a tela e a API nunca
discordam sobre o que é válido.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from catalogo.models import ItemCatalogo
from core.validators import UF_CHOICES
from empresas.models import Empresa
from engines.contratos import ANEXOS_SN
from simulacoes.serializers import TP_DOCS_SN

VAZIO = [("", "—")]


def _texto(rotulo, **kwargs):
    kwargs.setdefault("required", False)
    return forms.CharField(label=rotulo, **kwargs)


class DecimalBR(forms.DecimalField):
    """Aceita 1.234,56, 1234,56 e 1234.56 (o que o contador digita e o que vem de cenário duplicado)."""

    def to_python(self, value):
        if isinstance(value, str):
            texto = value.strip().replace(" ", "").replace("R$", "").replace("%", "")
            if "," in texto:
                texto = texto.replace(".", "").replace(",", ".")
            value = texto
        return super().to_python(value)


def _decimal(rotulo, casas=2, **kwargs):
    kwargs.setdefault("required", False)
    kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal"}))
    return DecimalBR(label=rotulo, decimal_places=casas, max_digits=18, **kwargs)


def _aliquota(rotulo, **kwargs):
    kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "%"}))
    return _decimal(rotulo, casas=4, **kwargs)


def aplicar_erros(form, erros, mapa=None):
    """Põe os erros de um serializer nos campos do form (ou no topo, se o campo não existir)."""
    mapa = mapa or {}
    for campo, mensagens in _achatar(erros):
        alvo = mapa.get(campo, campo)
        if alvo in form.fields:
            form.add_error(alvo, mensagens)
        else:
            prefixo = "" if campo in ("non_field_errors", "detail", "") else f"{campo}: "
            form.add_error(None, [f"{prefixo}{m}" for m in mensagens])


def _achatar(erros, prefixo=""):
    """{"itens": [{}, {"ncm": ["x"]}]} → [("itens.1.ncm", ["x"])]."""
    if isinstance(erros, dict):
        for chave, valor in erros.items():
            nome = f"{prefixo}.{chave}" if prefixo else str(chave)
            if chave in ("non_field_errors", "detail"):
                nome = prefixo
            yield from _achatar(valor, nome)
    elif isinstance(erros, list) and erros and all(isinstance(e, str) for e in erros):
        yield prefixo, [str(e) for e in erros]
    elif isinstance(erros, list):
        for posicao, valor in enumerate(erros):
            if valor:
                yield from _achatar(valor, f"{prefixo}.{posicao}" if prefixo else str(posicao))
    elif erros:
        yield prefixo, [str(erros)]


# --- Conta ---------------------------------------------------------------------------------------


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={"autofocus": True}))
    password = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput)

    error_messages = {
        "invalid_login": "E-mail ou senha incorretos.",
        "inactive": "Esta conta está inativa.",
    }


class CadastroForm(forms.Form):
    nome = forms.CharField(label="Seu nome", max_length=150)
    email = forms.EmailField(label="E-mail")
    senha = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput,
        strip=False,
        help_text="Mínimo de 10 caracteres, com letras e números.",
    )
    escritorio_razao_social = forms.CharField(label="Razão social do escritório", max_length=200)
    escritorio_documento = forms.CharField(label="CNPJ ou CPF do titular", max_length=18)


class NovoEscritorioForm(forms.Form):
    razao_social = forms.CharField(label="Razão social do escritório", max_length=200)
    documento = forms.CharField(label="CNPJ ou CPF do titular", max_length=18)


# --- Empresas ------------------------------------------------------------------------------------


class EmpresaForm(forms.Form):
    cnpj = forms.CharField(label="CNPJ", max_length=18)
    razao_social = forms.CharField(label="Razão social", max_length=200)
    nome_fantasia = _texto("Nome fantasia", max_length=200)
    regime = forms.ChoiceField(label="Regime", choices=Empresa.Regime.choices)
    crt = forms.TypedChoiceField(label="CRT", choices=Empresa.CRT.choices, coerce=int)
    cnae_principal = forms.CharField(label="CNAE principal", max_length=10)
    uf = forms.ChoiceField(label="UF", choices=UF_CHOICES)
    municipio_ibge = forms.CharField(
        label="Município (código IBGE)", max_length=7, help_text="7 dígitos. Ex.: 3550308 para São Paulo."
    )
    ativa = forms.BooleanField(label="Ativa", required=False, initial=True)


# --- Catálogo ------------------------------------------------------------------------------------


class ItemCatalogoForm(forms.Form):
    tipo = forms.ChoiceField(label="Tipo", choices=ItemCatalogo.Tipo.choices)
    codigo = forms.CharField(label="Código interno", max_length=60)
    descricao = forms.CharField(label="Descrição", max_length=300)
    ncm = _texto("NCM", max_length=10, help_text="Produto: 8 dígitos.")
    nbs = _texto("NBS", max_length=12, help_text="Serviço: 9 dígitos.")
    cst = _texto("CST", max_length=3)
    c_class_trib = forms.CharField(label="cClassTrib", max_length=8)
    c_ativ_sn = _texto("cAtivSN", max_length=2, help_text="Só para empresas do Simples.")


class ImportacaoForm(forms.Form):
    arquivo = forms.FileField(
        label="Planilha (.csv ou .xlsx)",
        help_text="Colunas: tipo, codigo, descricao, ncm, nbs, cst, cclasstrib, cativsn. Até 5.000 linhas.",
    )


# --- Alíquotas efetivas do Simples ---------------------------------------------------------------

ANEXOS_COM_ISS = ("III", "IV", "V")


class AliquotasSimplesForm(forms.Form):
    periodo = forms.CharField(
        label="Mês de apuração",
        widget=forms.DateInput(attrs={"type": "month"}),
        help_text="O mês do PGDAS-D de onde as alíquotas foram copiadas.",
    )
    fator_r = forms.BooleanField(label="Fator R (folha ≥ 28%)", required=False)
    impedimento_sublimite = forms.BooleanField(
        label="Impedido de recolher ICMS/ISS no Simples (sublimite)", required=False
    )
    observacao = _texto("Observação", max_length=300)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for anexo in ANEXOS_SN:
            self.fields[f"{anexo}_cbs"] = _aliquota(f"Anexo {anexo} · CBS")
            self.fields[f"{anexo}_ibs"] = _aliquota(f"Anexo {anexo} · IBS")
            if anexo in ANEXOS_COM_ISS:
                self.fields[f"{anexo}_sr_cbs"] = _aliquota(f"Anexo {anexo} · CBS sem redistribuição do ISS")
                self.fields[f"{anexo}_sr_ibs"] = _aliquota(f"Anexo {anexo} · IBS sem redistribuição do ISS")

    def linhas_anexos(self):
        """Para o template: uma linha por anexo, com os campos que ele tem."""
        for anexo in ANEXOS_SN:
            yield {
                "anexo": anexo,
                "cbs": self[f"{anexo}_cbs"],
                "ibs": self[f"{anexo}_ibs"],
                "sr_cbs": self[f"{anexo}_sr_cbs"] if anexo in ANEXOS_COM_ISS else None,
                "sr_ibs": self[f"{anexo}_sr_ibs"] if anexo in ANEXOS_COM_ISS else None,
            }

    def para_api(self):
        """Dados no formato do `AliquotaEfetivaSNSerializer`. Anexo sem nenhum valor fica de fora."""
        dados = self.cleaned_data
        anexos = {}
        for anexo in ANEXOS_SN:
            cbs, ibs = dados.get(f"{anexo}_cbs"), dados.get(f"{anexo}_ibs")
            sr_cbs, sr_ibs = dados.get(f"{anexo}_sr_cbs"), dados.get(f"{anexo}_sr_ibs")
            if cbs is None and ibs is None and sr_cbs is None and sr_ibs is None:
                continue
            anexos[anexo] = {"cbs": _str(cbs), "ibs": _str(ibs)}
            if sr_cbs is not None or sr_ibs is not None:
                anexos[anexo]["sem_redistribuicao_iss"] = {"cbs": _str(sr_cbs), "ibs": _str(sr_ibs)}
        return {
            "periodo": dados["periodo"],
            "fator_r": dados["fator_r"],
            "impedimento_sublimite": dados["impedimento_sublimite"],
            "observacao": dados.get("observacao", ""),
            "anexos": anexos,
        }

    MAPA_ERROS = {
        **{f"anexos.{a}.{c}": f"{a}_{c}" for a in ANEXOS_SN for c in ("cbs", "ibs")},
        **{f"anexos.{a}.sem_redistribuicao_iss.{c}": f"{a}_sr_{c}" for a in ANEXOS_COM_ISS for c in ("cbs", "ibs")},
    }

    @staticmethod
    def inicial_de(cadastro):
        inicial = {
            "periodo": f"{cadastro.periodo:%Y-%m}",
            "fator_r": cadastro.fator_r,
            "impedimento_sublimite": cadastro.impedimento_sublimite,
            "observacao": cadastro.observacao,
        }
        for anexo, valor in cadastro.anexos.items():
            inicial[f"{anexo}_cbs"] = valor.get("cbs")
            inicial[f"{anexo}_ibs"] = valor.get("ibs")
            if sr := valor.get("sem_redistribuicao_iss"):
                inicial[f"{anexo}_sr_cbs"] = sr.get("cbs")
                inicial[f"{anexo}_sr_ibs"] = sr.get("ibs")
        return inicial


def _str(valor):
    return None if valor is None else str(valor)


# --- Simulação -----------------------------------------------------------------------------------


class _ComCatalogo(forms.Form):
    def __init__(self, *args, catalogo=None, **kwargs):
        super().__init__(*args, **kwargs)
        campo = forms.ModelChoiceField(
            label="Item do catálogo", queryset=catalogo, required=False, empty_label="— digitar os códigos —"
        )
        self.fields = {"item_catalogo": campo, **self.fields}


class ItemRegularForm(_ComCatalogo):
    ncm = _texto("NCM", max_length=8)
    nbs = _texto("NBS", max_length=9)
    cst = _texto("CST", max_length=3)
    c_class_trib = _texto("cClassTrib", max_length=6)
    base_calculo = _decimal("Base de cálculo (R$)")
    quantidade = _decimal("Quantidade", casas=4)
    unidade = _texto("Unidade", max_length=6)


class ItemSimplesForm(_ComCatalogo):
    c_ativ_sn = _texto("cAtivSN", max_length=2)
    c_class_trib = _texto("cClassTrib", max_length=6)
    ncm = _texto("NCM", max_length=8)
    nbs = _texto("NBS", max_length=9)
    base_calculo = _decimal("Base de cálculo (R$)")
    valor_imposto_seletivo = _decimal("IS (R$)")
    trib_issqn = _texto("tribISSQN", max_length=2)
    tp_ret_issqn = _texto("tpRetISSQN", max_length=2)
    tp_bm = _texto("tpBM", max_length=2)


class _ItensFormSet(forms.BaseFormSet):
    def __init__(self, *args, catalogo=None, **kwargs):
        self.catalogo = catalogo
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "catalogo": self.catalogo}

    @property
    def empty_form(self):
        form = self.form(
            auto_id=self.auto_id,
            prefix=self.add_prefix("__prefix__"),
            empty_permitted=True,
            use_required_attribute=False,
            catalogo=self.catalogo,
            renderer=self.renderer,
        )
        self.add_fields(form, None)
        return form

    def preenchidos(self):
        """Itens com algum campo digitado, já no formato da API (sem campos vazios)."""
        itens = []
        for form in self.forms:
            if not form.has_changed() or not hasattr(form, "cleaned_data"):
                continue
            item = {}
            for campo, valor in form.cleaned_data.items():
                if valor in (None, ""):
                    continue
                item[campo] = str(valor.pk) if isinstance(valor, ItemCatalogo) else str(valor)
            if item:
                itens.append((form, item))
        return itens


ItensRegularFormSet = forms.formset_factory(ItemRegularForm, formset=_ItensFormSet, extra=1, max_num=100)
ItensSimplesFormSet = forms.formset_factory(ItemSimplesForm, formset=_ItensFormSet, extra=1, max_num=100)


class SimulacaoRegularForm(forms.Form):
    rotulo = _texto("Nome do cenário", max_length=120, help_text="Ex.: Cenário base, Com redução de 60%.")
    data = forms.DateField(label="Data do fato gerador", widget=forms.DateInput(attrs={"type": "date"}))
    hora = forms.TimeField(label="Hora", required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    uf = forms.ChoiceField(label="UF", choices=VAZIO + UF_CHOICES, required=False, help_text="Padrão: UF da empresa.")
    municipio = _texto("Município (IBGE)", max_length=7, help_text="Padrão: município da empresa.")
    tp_doc = forms.IntegerField(label="Tipo de documento (tpDoc)", required=False, min_value=1)
    cbs = _aliquota("CBS (%)")
    ibs_estadual = _aliquota("IBS estadual (%)")
    ibs_municipal = _aliquota("IBS municipal (%)")


class SimulacaoSimplesForm(forms.Form):
    rotulo = _texto("Nome do cenário", max_length=120)
    data = forms.DateField(label="Data da operação", widget=forms.DateInput(attrs={"type": "date"}))
    tp_doc = forms.ChoiceField(
        label="Documento",
        choices=[
            (
                c,
                {
                    "55": "55 · NF-e",
                    "65": "65 · NFC-e",
                    "57": "57 · CT-e",
                    "67": "67 · CT-e OS",
                    "63": "63 · BP-e",
                    "93": "93",
                    "62": "62 · NFCom",
                    "91": "91 · NFS-e",
                }.get(c, c),
            )
            for c in TP_DOCS_SN
        ],
    )


# --- Documentos fiscais --------------------------------------------------------------------------


class VariosArquivosInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class VariosArquivosField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", VariosArquivosInput(attrs={"multiple": True, "accept": ".xml,.zip"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(VariosArquivosField, self).clean(d, initial) for d in data] or self.clean(None)
        return [super().clean(data, initial)]


class ImportarXmlForm(forms.Form):
    arquivos = VariosArquivosField(
        label="XMLs de NF-e, NFS-e ou um .zip",
        help_text="NF-e autorizada (nfeProc), NFe ou enviNFe, ou NFS-e nacional (NFSe). "
        "Até 200 notas por envio; .zip de até 50 MB.",
    )


class DivergenciaForm(forms.Form):
    status = forms.ChoiceField(label="Situação", choices=[])
    comentario = forms.CharField(label="Comentário", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        from documentos.models import Divergencia

        super().__init__(*args, **kwargs)
        self.fields["status"].choices = Divergencia.Status.choices

    def clean(self):
        dados = super().clean()
        if dados.get("status") == "justificada" and not (dados.get("comentario") or "").strip():
            self.add_error("comentario", "Explique por que a diferença está correta.")
        return dados


class ToleranciaForm(forms.Form):
    tolerancia = DecimalBR(
        label="Tolerância (R$)", min_value=0, max_value=1000, decimal_places=2, max_digits=8,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )  # fmt: skip


SITUACOES_DANFSE = [("0", "Válida"), ("1", "Substituída"), ("2", "Cancelada")]


class DanfseForm(forms.Form):
    situacao = forms.ChoiceField(label="Situação impressa", choices=SITUACOES_DANFSE, initial="0")
    canhoto = forms.BooleanField(label="Incluir canhoto de recebimento", required=False)


class DanfseAvulsoForm(DanfseForm):
    arquivo = forms.FileField(label="XML da NFS-e nacional autorizada", help_text="Arquivo .xml de até 5 MB.")

    field_order = ["arquivo", "situacao", "canhoto"]

    def clean_arquivo(self):
        from documentos.servicos import MAX_BYTES_XML

        arquivo = self.cleaned_data["arquivo"]
        if arquivo.size > MAX_BYTES_XML:
            raise forms.ValidationError("Arquivo maior que 5 MB.")
        return arquivo.read()
