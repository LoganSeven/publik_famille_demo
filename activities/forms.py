# activities/forms.py
from django import forms
from .models import Enrollment
from families.models import Child

class EnrollmentForm(forms.ModelForm):
    # Ajout de browser-default pour rendre le select visible sans init JS
    child = forms.ModelChoiceField(
        queryset=Child.objects.none(),
        label='Enfant',
        widget=forms.Select(attrs={'class': 'browser-default'})
    )

    class Meta:
        model = Enrollment
        fields = ['child']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user and user.is_authenticated:
            self.fields['child'].queryset = Child.objects.filter(parent=user)
        else:
            self.fields['child'].queryset = Child.objects.none()
