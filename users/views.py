from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .forms import UserRegisterForm, UserUpdateForm
from django.shortcuts import render,redirect
from django.contrib.auth import login,authenticate
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from movies.models import Movie , Booking, Order
from movies import discovery

def home(request):
    if request.user.is_authenticated:
        movies = discovery.recommended_for_user(request.user, limit=8)
    else:
        movies = discovery.trending_fallback(8)

    carousel_movies = Movie.objects.order_by("-release_date")[:5]

    return render(request, 'home.html', {'movies': movies, 'carousel_movies': carousel_movies})
def register(request):
    if request.method == 'POST':
        form=UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username=form.cleaned_data.get('username')
            password=form.cleaned_data.get('password1')
            user=authenticate(username=username,password=password)
            login(request,user)
            return redirect('profile')
    else:
        form=UserRegisterForm()
    return render(request,'users/register.html',{'form':form})

def login_view(request):
    if request.method == 'POST':
        form=AuthenticationForm(request,data=request.POST)
        if form.is_valid():
            user=form.get_user()
            login(request,user)
            return redirect('/')
    else:
        form=AuthenticationForm()
    return render(request,'users/login.html',{'form':form})

@login_required
def profile(request):
    bookings = Booking.objects.filter(user=request.user).select_related(
        "movie", "theater", "schedule", "seat", "payment", "refund"
    ).order_by("-booked_at")

    orders = (
        Order.objects.filter(user=request.user)
        .select_related("schedule", "schedule__movie")
        .prefetch_related("payments")
        .order_by("-created_at")
    )

    stats = Order.objects.filter(user=request.user, status="paid").aggregate(
        total_spent=Sum("amount"), paid_order_count=Count("id")
    )
    profile_stats = {
        "total_spent": stats["total_spent"] or 0,
        "active_bookings": bookings.filter(is_cancelled=False).count(),
        "member_since": request.user.date_joined,
    }

    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        if u_form.is_valid():
            u_form.save()
            return redirect('profile')
    else:
        u_form = UserUpdateForm(instance=request.user)

    return render(request, 'users/profile.html', {
        'u_form': u_form,
        'bookings': bookings,
        'orders': orders,
        'profile_stats': profile_stats,
    })

@login_required
def reset_password(request):
    if request.method == 'POST':
        form=PasswordChangeForm(user=request.user,data=request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form=PasswordChangeForm(user=request.user)
    return render(request,'users/reset_password.html',{'form':form})