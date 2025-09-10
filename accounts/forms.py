
from django import forms
from django.contrib.auth.models import User

class SignUpForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'validate'}), label='Mot de passe')
    password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'validate'}), label='Confirmez le mot de passe')

    class Meta:
        model = User
        fields = ['username', 'email', 'password']
        labels = { 'username': 'Nom d\'utilisateur', 'email':'E-mail' }
        widgets = {
            'username': forms.TextInput(attrs={'class': 'validate'}),
            'email': forms.EmailInput(attrs={'class': 'validate'}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password') != cleaned.get('password_confirm'):
            self.add_error('password_confirm', 'Les mots de passe ne correspondent pas.')
        return cleaned
