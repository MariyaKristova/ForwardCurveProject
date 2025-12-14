from django.shortcuts import render


def custom_404(request, exception):
    return render(request, "plotapp/errors/404.html", status=404)

def custom_500(request):
    return render(request, "plotapp/errors/500.html", status=500)