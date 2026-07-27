from django import forms

from friends.models import Friendship


class GroupCreateForm(forms.Form):
    name = forms.CharField(max_length=80, widget=forms.TextInput(attrs={'placeholder': 'e.g. Weekend Trip 🏔️'}))
    members = forms.MultipleChoiceField(choices=(), widget=forms.CheckboxSelectMultiple, required=True)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        friends = Friendship.friends_of(user) if user else []
        self.fields['members'].choices = [(f.pk, f.username) for f in friends]

    def clean_members(self):
        ids = self.cleaned_data['members']
        if not ids:
            raise forms.ValidationError('Pick at least one friend to add.')
        return ids


class AddMembersForm(forms.Form):
    members = forms.MultipleChoiceField(choices=(), widget=forms.CheckboxSelectMultiple, required=True)

    def __init__(self, *args, user=None, exclude_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        friends = Friendship.friends_of(user) if user else []
        available = [f for f in friends if f.pk not in exclude_ids]
        self.fields['members'].choices = [(f.pk, f.username) for f in available]

    def clean_members(self):
        ids = self.cleaned_data['members']
        if not ids:
            raise forms.ValidationError('Pick at least one friend to add.')
        return ids


class GroupSettingsForm(forms.Form):
    name = forms.CharField(max_length=80)
    photo = forms.ImageField(required=False, help_text='JPG or PNG, up to 8MB.')

    def clean_photo(self):
        file = self.cleaned_data.get('photo')
        if file and file.size > 8 * 1024 * 1024:
            raise forms.ValidationError('That image is too large — please use one under 8MB.')
        return file
