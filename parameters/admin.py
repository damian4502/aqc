from django import forms
from django.contrib import admin

from .models import Parameter


class ParameterAdminForm(forms.ModelForm):
    class Meta:
        model = Parameter
        fields = "__all__"
        widgets = {
            "formula": forms.Textarea(attrs={"rows": 4, "cols": 80}),
            "color": forms.TextInput(attrs={"placeholder": "#38bdf8", "size": 10}),
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

    def clean_color(self):
        from dashboard.chart_colors import normalize_color

        raw = (self.cleaned_data.get("color") or "").strip()
        if not raw:
            return ""
        color = normalize_color(raw)
        if not color:
            raise forms.ValidationError("Use a hex color such as #38bdf8, or leave it empty.")
        return color


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    form = ParameterAdminForm
    list_display = ["name", "unit", "view_count", "kind", "color", "description"]
    readonly_fields = ["view_count"]
    search_fields = ["name", "formula"]
    list_filter = ["name"]

    @admin.display(description="Kind")
    def kind(self, obj):
        if obj.is_room_bound:
            return "Per room"
        if obj.is_calculated:
            return "Calculated"
        return "Measured"
