from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from contas import views as contas
from core.views import healthz
from empresas.views import EmpresaViewSet

router = DefaultRouter(trailing_slash=True)
router.register("empresas", EmpresaViewSet, basename="empresa")
router.register("membros", contas.MembroViewSet, basename="membro")
router.register("convites", contas.ConviteViewSet, basename="convite")

auth = [
    path("csrf/", contas.CsrfView.as_view(), name="auth-csrf"),
    path("cadastro/", contas.CadastroView.as_view(), name="auth-cadastro"),
    path("login/", contas.LoginView.as_view(), name="auth-login"),
    path("logout/", contas.LogoutView.as_view(), name="auth-logout"),
    path("eu/", contas.EuView.as_view(), name="auth-eu"),
    path("escritorio/", contas.SelecionarEscritorioView.as_view(), name="auth-selecionar-escritorio"),
    path("senha/esqueci/", contas.EsqueciSenhaView.as_view(), name="auth-esqueci-senha"),
    path("senha/redefinir/", contas.RedefinirSenhaView.as_view(), name="auth-redefinir-senha"),
    path("senha/alterar/", contas.AlterarSenhaView.as_view(), name="auth-alterar-senha"),
]

api = [
    path("auth/", include(auth)),
    path("escritorio/", contas.EscritorioAtualView.as_view(), name="escritorio-atual"),
    path("convites/consultar/", contas.ConsultarConviteView.as_view(), name="convite-consultar"),
    path("convites/aceitar/", contas.AceitarConviteView.as_view(), name="convite-aceitar"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("api/", include(api)),
]
