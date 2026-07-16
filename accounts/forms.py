import random
from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User

PALETTE = ['#6C63FF', '#FF6584', '#2EC4B6', '#FF9F1C', '#3A86FF', '#8338EC']


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.avatar_color = random.choice(PALETTE)
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('bio',)
        widgets = {
            'bio': forms.TextInput(attrs={'placeholder': "Say something about yourself…", 'maxlength': 160}),
        }
