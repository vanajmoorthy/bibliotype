from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class CustomUserCreationForm(UserCreationForm):
    # We add the fields we want to collect
    first_name = forms.CharField(max_length=30, required=True, help_text="Your first name or a nickname.")
    email = forms.EmailField(required=True, help_text="Required. We will never share your email.")

    class Meta(UserCreationForm.Meta):
        model = User
        # The fields that will be displayed on the form
        fields = ("first_name", "email")

    def save(self, commit=True):
        """
        This is the magic. When the form is saved, we take the email
        and set it as the username, and also save the first_name.
        """
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]  # Use email as username
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        if commit:
            user.save()
        return user
