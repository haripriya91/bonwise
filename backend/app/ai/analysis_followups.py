"""Bounded context/date resolution. Context contains filters, never financial facts."""
import calendar
from datetime import date
import re

MONTHS = {
    'january':1,'januar':1,'february':2,'februar':2,'march':3,'märz':3,'maerz':3,
    'april':4,'may':5,'mai':5,'june':6,'juni':6,'july':7,'juli':7,'august':8,
    'september':9,'sept':9,'october':10,'oktober':10,'november':11,'december':12,'dezember':12,
}
MONTH_PATTERN = '|'.join(sorted(MONTHS, key=len, reverse=True))
MONTH_YEAR = re.compile(r'\b(?:(?P<month>'+MONTH_PATTERN+r')\s+(?P<year>20\d{2})|(?P<year_first>20\d{2})\s+(?P<month_after>'+MONTH_PATTERN+r'))\b', re.I)
MERCHANT = {'which shop was that','which store was that','which merchant was that','where was that purchase','where did i buy it','which shop','welcher laden war das','welches geschäft war das','bei welchem händler war das'}
PURCHASE_DATE = {'what date was that purchase','when was that purchase','when did i buy it','what date was it','wann war dieser einkauf','an welchem datum war der einkauf'}
RECORDS = {'show me the purchase details','show purchase details','show those purchases','which purchases contributed to that spending','which purchases contributed to that','show me those purchases','zeige diese einkäufe'}
BUDGET = {'what budget was set for that same period','what budget was set for that period','what was my budget for that period','what is my budget for this period','welches budget galt für diesen zeitraum'}


def resolve(question, plan, memory, body):
    """Only exact, harmless follow-ups are repaired; all data stays household-scoped."""
    clean = re.sub(r'[?.!]+$', '', question.strip().casefold()).strip()
    kind = ('merchant' if clean in MERCHANT else 'date' if clean in PURCHASE_DATE
            else 'records' if clean in RECORDS else 'budget' if clean in BUDGET else None)
    previous = memory.last_plan if memory else None
    previous_ok = previous and previous.operation not in {'unsupported','clarify'}
    page_changed = bool(memory and memory.selected_period and
        (memory.selected_period.start_date != body.start_date or memory.selected_period.end_date != body.end_date))
    if kind:
        if not previous_ok and kind in {'merchant','date'}:
            return plan.model_copy(update=dict(operation='clarify', detail=kind,
                clarification_reason='scope', clarification_target='records',
                product_query=None, merchant_query=None, category=None))
        start = previous.start_date if previous_ok and not page_changed else body.start_date
        end = previous.end_date if previous_ok and not page_changed else body.end_date
        values = dict(operation='budget' if kind=='budget' else 'records',
            detail=kind if kind in {'merchant','date'} else None,
            start_date=start, end_date=end, comparison_start_date=None, comparison_end_date=None,
            clarification_reason='none', clarification_target=None, sort_direction='highest',
            metric='spending', top_n=10, category=None, product_query=None, merchant_query=None, quantity_unit=None)
        if previous_ok and kind != 'budget':
            values.update(category=memory.focus_category or previous.category,
                product_query=memory.focus_product or previous.product_query,
                merchant_query=memory.focus_merchant or previous.merchant_query,
                quantity_unit=previous.quantity_unit)
        plan = plan.model_copy(update=values)
    # Explicit single month/year takes precedence over selected page dates.
    # Do not rewrite comparisons, a request containing multiple months, or day ranges.
    matches = list(MONTH_YEAR.finditer(question))
    if (len(matches)==1 and plan.operation not in {'clarify','unsupported','compare'}
            and not re.search(r'\b\d{1,2}[./-]\d{1,2}|\b\d{4}-\d{2}-\d{2}|\b\d{1,2}\s+(?:'+MONTH_PATTERN+r')\b|\b(?:from|between|vom|zwischen|since|seit|until|bis)\b', question, re.I)):
        match = matches[0]
        year = int(match['year'] or match['year_first'])
        month = MONTHS[(match['month'] or match['month_after']).casefold()]
        plan = plan.model_copy(update=dict(start_date=date(year,month,1),
            end_date=date(year,month,calendar.monthrange(year,month)[1])))
    return plan
