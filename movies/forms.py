from django import forms
from .models import Review, ReviewReport


class ReviewForm(forms.ModelForm):
    rating = forms.ChoiceField(
        choices=[(i, f"{i}") for i in range(10, 0, -1)],
        widget=forms.RadioSelect,
    )

    class Meta:
        model = Review
        fields = ["rating", "review"]
        widgets = {
            "review": forms.Textarea(
                attrs={
                    "rows": 4,
                    "class": "form-control",
                    "placeholder": "Share your thoughts about the movie...",
                }
            ),
        }
        labels = {
            "review": "Your Review",
        }


class ReviewReportForm(forms.ModelForm):
    class Meta:
        model = ReviewReport
        fields = ["reason"]
        widgets = {
            "reason": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": "form-control",
                    "placeholder": "Why are you reporting this review?",
                }
            ),
        }
        labels = {
            "reason": "Reason for report",
        }
