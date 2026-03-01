from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class CustomUserCreationForm(UserCreationForm):

    email = forms.EmailField(required=True, help_text="Required. Used for login and account recovery.")

    username = forms.CharField(
        label="Display Name",
        max_length=15,
        required=True,
        help_text="Required. Your public name (15 characters or fewer, case-insensitive).",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if username and User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This display name is already taken. Please choose another.")
        return username.lower()

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists.")
        return email


class UpdateDisplayNameForm(forms.ModelForm):

    username = forms.CharField(label="New Display Name", max_length=15, help_text="15 characters or fewer.")

    class Meta:
        model = User
        fields = ["username"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_username(self):
        new_username = self.cleaned_data.get("username")

        if new_username and User.objects.filter(username__iexact=new_username).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("This display name is already taken. Please choose another.")

        return new_username.lower()


class UpdateEmailForm(forms.Form):
    email = forms.EmailField(label="New Email", max_length=254)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("An account with this email address already exists.")
        return email


class ChangePasswordForm(forms.Form):
    old_password = forms.CharField(widget=forms.PasswordInput)
    new_password1 = forms.CharField(widget=forms.PasswordInput, label="New Password")
    new_password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm New Password")

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        old_password = self.cleaned_data.get("old_password")
        if not self.user.check_password(old_password):
            raise forms.ValidationError("Your current password is incorrect.")
        return old_password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("new_password1")
        p2 = cleaned_data.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "The two password fields didn't match.")
        if p1:
            password_validation.validate_password(p1, self.user)
        return cleaned_data
