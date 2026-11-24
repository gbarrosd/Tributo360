"""Login, cadastro, escolha e criação de escritório, tela inicial e painel dos motores."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from auditoria.servicos import ip_da_requisicao
from contas import servicos as contas_servicos
from contas.models import Escritorio
from contas.serializers import CadastroSerializer
from core.tenancy import SESSAO_ESCRITORIO
from core.validators import limpar_documento, validar_cpf_ou_cnpj
from empresas.models import Empresa
from engines.models import SincronizacaoTabela, VersaoMotor
from engines.versoes import versoes_dos_motores
from simulacoes.models import Simulacao

from ..acesso import destino_seguro, tela
from ..forms import CadastroForm, LoginForm, NovoEscritorioForm, aplicar_erros


def _memberships_ativas(usuario):
    return (
        usuario.memberships.filter(ativo=True)
        .exclude(escritorio__status=Escritorio.Status.CANCELADO)
        .select_related("escritorio")
        .order_by("escritorio__razao_social")
    )


class LoginView(auth_views.LoginView):
    template_name = "web/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def _chave(self):
        return f"web-login:{ip_da_requisicao(self.request)}"

    def post(self, request, *args, **kwargs):
        tentativas = cache.get(self._chave(), 0)
        if tentativas >= settings.WEB_LOGIN_TENTATIVAS:
            form = self.get_form()
            form.errors.clear()
            form.add_error(None, "Muitas tentativas de login. Espere um minuto e tente de novo.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        chave = self._chave()
        cache.add(chave, 0, settings.WEB_LOGIN_JANELA_SEGUNDOS)
        try:
            cache.incr(chave)
        except ValueError:
            cache.set(chave, 1, settings.WEB_LOGIN_JANELA_SEGUNDOS)
        return super().form_invalid(form)

    def form_valid(self, form):
        resposta = super().form_valid(form)
        ativas = list(_memberships_ativas(self.request.user)[:2])
        if len(ativas) == 1:
            self.request.session[SESSAO_ESCRITORIO] = str(ativas[0].escritorio_id)
            return resposta
        # Nenhum ou vários escritórios: a pessoa escolhe (ou cria o primeiro).
        return redirect("web:escritorios")


@require_POST
def sair(request):
    logout(request)
    return redirect("web:login")


def cadastro(request):
    """Cria conta + escritório trial, igual a POST /api/auth/cadastro/."""
    if request.user.is_authenticated:
        return redirect("web:inicio")
    form = CadastroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        serializer = CadastroSerializer(data=form.cleaned_data)
        if serializer.is_valid():
            usuario, membership = contas_servicos.cadastrar_escritorio(request, serializer.validated_data)
            login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
            request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
            messages.success(request, "Conta criada. Cadastre a primeira empresa da carteira.")
            return redirect("web:empresa-nova")
        aplicar_erros(form, serializer.errors)
    return render(request, "web/cadastro.html", {"form": form})


@login_required
def escritorios(request):
    """Lista os escritórios do usuário, troca o ativo (POST) e oferece criar um quando não há nenhum."""
    ativas = list(_memberships_ativas(request.user))
    if request.method == "POST":
        escolhido = request.POST.get("escritorio")
        membership = next((m for m in ativas if str(m.escritorio_id) == escolhido), None)
        if membership is None:
            raise Http404("Escritório não encontrado.")
        request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
        messages.success(request, f"Agora você está no escritório {membership.escritorio.razao_social}.")
        return redirect(destino_seguro(request, request.POST.get("proximo"), "web:inicio"))
    pode_criar = not ativas or request.user.is_superuser
    return render(
        request,
        "web/escritorios.html",
        {"memberships": ativas, "form": NovoEscritorioForm() if pode_criar else None},
    )


@login_required
@require_POST
def escritorio_novo(request):
    """Primeiro escritório de quem entrou sem nenhum (ex.: superuser do `createsuperuser`).

    Quem já participa de um escritório só cria outro se for superuser: escritório novo é conta nova,
    com trial próprio.
    """
    if _memberships_ativas(request.user).exists() and not request.user.is_superuser:
        raise Http404()
    form = NovoEscritorioForm(request.POST)
    if form.is_valid():
        documento = limpar_documento(form.cleaned_data["documento"])
        try:
            validar_cpf_ou_cnpj(documento)
        except Exception as exc:  # ValidationError do Django
            form.add_error("documento", list(getattr(exc, "messages", [str(exc)])))
        else:
            membership = contas_servicos.criar_escritorio_para(
                request, request.user, form.cleaned_data["razao_social"], documento
            )
            request.session[SESSAO_ESCRITORIO] = str(membership.escritorio_id)
            messages.success(request, f"Escritório {membership.escritorio.razao_social} criado. Você é o dono.")
            return redirect("web:inicio")
    return render(
        request, "web/escritorios.html", {"memberships": list(_memberships_ativas(request.user)), "form": form}
    )


@tela()
def inicio(request):
    empresas = Empresa.tenant(request).filter(ativa=True)
    recentes = Simulacao.tenant(request).select_related("empresa", "versao_motor")[:8]
    return render(
        request,
        "web/inicio.html",
        {"total_empresas": empresas.count(), "empresas": empresas[:8], "recentes": recentes},
    )


@tela()
def motores(request):
    """Painel de versões (mesmo conteúdo de GET /api/motores/versoes/)."""
    return render(
        request,
        "web/motores.html",
        {
            "atuais": versoes_dos_motores(),
            "historico": VersaoMotor.objects.all()[:50],
            "sincronizacao": SincronizacaoTabela.objects.select_related("versao_motor").first(),
        },
    )
