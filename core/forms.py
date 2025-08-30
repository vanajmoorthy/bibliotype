from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class CustomUserCreationForm(UserCreationForm):
    """
    A custom form for user signup that collects a unique display name (username)
    and an email address.
    """

    # We rename the fields here for clarity in the template
    username = forms.CharField(
        max_length=30,
        required=True,
        help_text="Required. 30 characters or fewer. Letters, digits and @/./+/-/_ only. Don't worry, you can change this later!",
        label="Display Name",  # This is how it will appear on the page
    )
    email = forms.EmailField(required=True, help_text="Required. Will be used for login and account recovery.")

    class Meta(UserCreationForm.Meta):
        model = User
        # The fields that will be displayed on the form, in order.
        fields = ("username", "email")

    def clean_email(self):
        """
        Ensure the email is unique in the system.
        """
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise ValidationError("An account with this email address already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["username"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class UpdateDisplayNameForm(forms.ModelForm):
    """
    A form for users to update their public display name (username).
    """

    class Meta:
        model = User
        fields = ["username"]
        labels = {
            "username": "New Display Name",
        }

    def __init__(self, *args, **kwargs):
        # We need to know who the current user is to check for uniqueness correctly
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_username(self):
        """
        Ensure the new username is unique, excluding the current user's
        existing username.
        """
        new_username = self.cleaned_data.get("username")
        # If the username hasn't changed, it's valid.
        if new_username == self.user.username:
            return new_username

        # Check if any OTHER user already has this username.
        if User.objects.filter(username=new_username).exists():
            raise ValidationError("This display name is already taken. Please choose another.")

        return new_username
