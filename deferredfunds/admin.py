from django.contrib import admin

from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal

admin.site.register(DeferredFundAccount)
admin.site.register(DeferredFundEntry)
admin.site.register(DeferredFundWithdrawal)
