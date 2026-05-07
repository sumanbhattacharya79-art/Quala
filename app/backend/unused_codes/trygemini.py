from google import genai
import os

api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents="""You are a 5 star
portfolio manager. You goal is to build 
investment portfolio for the user. 
These are the user provided input:
{Risk appetite: 20\% drawdown is ok. 
Retirement timeline: 10 years. 
Big spending: 1 M in 3 years, 
500 K in 10 years. Current investment value:
 2700000.}
Take the user provided 
input as context and what user want.Assume the user wants growth till retirement
 and then a risk-off income based strategy after retirement.
 Make any assumptions
you need to make beyond the user provided input. 
Then build 3 portfolios- 1) conservative 
2) moderate 3) aggresive but within limits,that 
satisfy the user 
provided criteria. Validate your suggested portfolio
using backtesting, drawdown and monte carlo simulation.
Clearly explain why you chose certain investments 
and how it helps the user. Explain your assumptions
and the date range of the daa you used to do any analysis """
)
print(response.text)
