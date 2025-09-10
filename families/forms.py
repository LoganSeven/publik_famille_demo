# families/forms.py
from django import forms
from .models import Child

class ChildForm(forms.ModelForm):
    class Meta:
        model = Child
        fields = ['first_name', 'last_name', 'birth_date']
        labels = { 'first_name':'Prénom', 'last_name':'Nom', 'birth_date':'Date de naissance' }
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'validate'}),
            'last_name': forms.TextInput(attrs={'class': 'validate'}),
            'birth_date': forms.DateInput(attrs={'type': 'date', 'class': 'validate'}),
        }
