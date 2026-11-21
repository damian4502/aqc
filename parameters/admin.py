from django import forms
from django.contrib import admin

from .models import Parameter


class ParameterAdminForm(forms.ModelForm):
    class Meta:
        model = Parameter
        fields = "__all__"
        widgets = {
            "formula": forms.Textarea(attrs={"rows": 4, "cols": 80}),
        }

    def clean_formula(self):
        text = (self.cleaned_data.get("formula") or "").strip()
        if not text:
            return ""
        from dashboard.parameter_formula import FormulaError, validate_parameter_formula
        from rooms.models import Room

        rooms = list(Room.objects.values_list("id", "name"))
        name = (self.cleaned_data.get("name") or self.instance.name or "").strip()
        parameters = []
        for parameter_id, param_name in Parameter.objects.values_list("id", "name"):
            if self.instance.pk and parameter_id == self.instance.pk:
                param_name = name
            parameters.append((parameter_id, param_name))
        parameter_id = self.instance.pk or 0
        if parameter_id == 0:
            parameters.append((0, name))
        formulas = {
            row_id: (formula or "")
            for row_id, formula in Parameter.objects.values_list("id", "formula")
        }
        formulas[parameter_id] = text
        try:
            validate_parameter_formula(text, rooms, parameters, formulas, parameter_id)
        except FormulaError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return text


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    form = ParameterAdminForm
    list_display = ["name", "unit", "kind", "description"]
    search_fields = ["name", "formula"]
    list_filter = ["name"]

    @admin.display(description="Kind")
    def kind(self, obj):
        return "Calculated" if obj.is_calculated else "Measured"
