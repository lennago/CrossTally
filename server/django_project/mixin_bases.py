"""Base types for mixins. They exist only for the type checker.

A mixin like 'PfadMixin' calls 'super().get_context_data()' and reads
'self.request' without inheriting from a view itself. From what, only the
class that mixes it in decides. The type checker therefore sees only 'object'
and reports each of these accesses. Here, per kind of mixin, stands *what it
assumes*.

At runtime each of these bases is simply 'object': nothing changes about the
inheritance and its order, and a mixin pulls in no view class it does not need.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django import forms
    from django.contrib import admin
    from django.views.generic import DeleteView, View
    from django.views.generic.base import ContextMixin
    from django.views.generic.detail import SingleObjectMixin, SingleObjectTemplateResponseMixin
    from django.views.generic.edit import ModelFormMixin, ProcessFormView

    FormularBasis = forms.BaseForm
    ModellformularBasis = forms.ModelForm
    ModelAdminBasis = admin.ModelAdmin
    LoeschansichtBasis = DeleteView

    class AnsichtBasis(ContextMixin, View[Any]):
        """Every view with a context: lists, detail views, forms."""

    class EinzelansichtBasis(SingleObjectMixin, View[Any]):
        """Views over exactly one row ('get_object')."""

    class ModellformularAnsichtBasis(
        SingleObjectTemplateResponseMixin, ModelFormMixin, ProcessFormView
    ):
        """The common base of 'CreateView' and 'UpdateView'."""

else:
    FormularBasis = ModellformularBasis = ModelAdminBasis = LoeschansichtBasis = object
    AnsichtBasis = EinzelansichtBasis = ModellformularAnsichtBasis = object
