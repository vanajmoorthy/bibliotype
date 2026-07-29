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
        # Intentionally does NOT reject duplicate emails. Revealing which addresses
        # already have accounts is a user-enumeration vector. The duplicate-email
        # path is handled in `signup_view` (US-017): a password-reset email is
        # sent to the legitimate owner and the user is redirected to the generic
        # "check your inbox" page, identical to the new-signup path's tone.
        # Normalize to lowercase so case-variants can't create lookalike duplicates.
        return self.cleaned_data.get("email", "").strip().lower()


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
    email = forms.EmailField(required=True)
    current_password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data.get("current_password")
        if self.user and not self.user.check_password(current):
            raise forms.ValidationError("Your current password is incorrect.")
        return current

    def clean_email(self):
        # Normalize to lowercase so case-variants can't create lookalike duplicates.
        email = self.cleaned_data.get("email", "").strip().lower()
        if self.user and User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("That email is already in use.")
        return email


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput)
    new_password1 = forms.CharField(widget=forms.PasswordInput)
    new_password2 = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data.get("current_password")
        if self.user and not self.user.check_password(current):
            raise forms.ValidationError("Your current password is incorrect.")
        return current

    def clean(self):
        cleaned_data = super().clean()
        new1 = cleaned_data.get("new_password1")
        new2 = cleaned_data.get("new_password2")
        if new1 and new2 and new1 != new2:
            raise forms.ValidationError("The two new passwords do not match.")
        if new1 and self.user:
            try:
                password_validation.validate_password(new1, self.user)
            except forms.ValidationError as e:
                raise forms.ValidationError(list(e.messages))
        return cleaned_data
