### Poject Idea
Hiking Planner

I'm living in san franciso bay area and we often go hiking on weekend to enjoy good weather. We have a hiking group and we regularly organize hiking events. One of organizer's task is to propose a hiking plan according to preferences of most group members, and remember hiking route, check weather conditions and trail conditions, and at the end, lead the group to hike safely. I would like AI can take part of organizer role by proposing a plan, generating a trip summary, check weather and trail conditions, and send out the final schedule.

Can ChatGpt do it? Partly yes. Once I asked ChatGpt with "can you give me a 6-mile hiking trip near north San Jose with greaet ocean view?", then ChatGpt recommended a few tours which look nice at first glance. ChatGpt then printed out a trail sequence for a tour when I asked. Then I checked the trail sequence on map and found out they were wrong. It says when arriving the intersection of trail A & trail B, turn left to trail B. And on map, trail A and trail B are not connected!

## Solution
There are a lot of hiking reports / reviews on web. At fist I want to use llm to scan those reports and extract hiking trails information and use them to generate hiking routes, but I found it is either out of scope of llm or too hard to sove. For this class project,I reduced the complexity to just search those reports and retrieve one that meet our requirement best, then we can use llm to generate a summary, extract trail sequnece and so on. Becuase there is no new route created, the trail sequence is 100% from the report and therefore should be accurate (because I know which data source is reliable based on my hiking experience).

## Audience
Day hikers living in San Francisco Bay Area.

### Task 1: Defining Problem, Audience, and Scope
the problem and audence are as above. I limit this project to just existing hiking tours around San Francisco Bay Area.

### Task 2: Propose a Solution
Crawling hiking reports from websites whose contents are reliable based on my experience. Use them to create a RAG application, which basically a chat app. Users can post request like "find a hiking route for me near Sunnyvale", "I heard there is a hiking trail leading to a cave. Can you find it for me?", "What is a good place to hike during winter?". The app searches its database, finds ra ecord matching user's request best and sends to llm to generate a summary, trail sequence, parking information and so on.

### Task 3: Dealing with the Data
Crawling hiking reports from websites whose contents are reliable based on my experience. Split each reports (in html) to smaller chunks and save them to QDrantVectorStore as we learnt in the class.

### Task 4: Building an End-to-End Agentic RAG Prototype
Please refer to [LangGraph diagram](https://github.com/diamond123/hiking-planner-ii/blob/main/backend/graph.png) for a diagram of this RAG application.
It works as follows.
- user posts a request like "find a hiking route for me near Sunnyvale".
- check if there is enough information for planning, including hiking date, preferred location, perferred view, total distance, difficulty level etc.
- if not, or preferences are incorrect (e.g. a hiking date in the past, unrealistic elevation gain), ask user to provide or re-enter those info. (for now, hiking date and preferred location are required)
- Check weather conditions for the date and location. If Weather is bad, ask user to choose another date or location.
- Search QDrantVectorStore to find chunks that best matches user's requests.
- Retreive the original hiking report using the chunk's metadata (So the chunks need to keep their origin in metadata) (*This is important* because we need to make sure the hiking plan workable and the trail sequence correct. no mistake is allowed.)
- Check trail conditions using TavilySearch and llm judge to see if it is OK to hike.
- If the trail condtions do not allow to hike (such as trail closure), go back to Search QDrantVectorStore to find next best option.
- The total attempt should be limitted (<=4 times). If no hiking tours are available, just let users know.
- If everything is good, send the original hiking report, weather and trail conditions information to llm to generate a final plan

### Task 5: Evals
We can use RAGAS to evaluate the RAG. But as I'm doing this project, I found it is not a standard RAG, because there is no much connections among chunks, and all chunks are similar at some level. E.g. if user's request is a general one like "find me a place to hike 5 miles with great view", then almost all chunks give similarity scores which are just slightly different.

As a hiking plan, accuracy is very important. I used RAGAS to evalute a few aspects such as context recall, failthfulness, answer accuracy, noise sensitivity as following. I think there is some setup issues with RAGAS to generate test dataset. the answer_accuracy is obiviously wrong. I don't have enough time to investigate it further. Anyway, the fithfulness is high which is what I want (we don't want a hiking plan that does not work)

testset:
| user_input | response | reference |
| --- | --- | --- | --- |
| How do I get to the Lagooon Area at Miller/Kno... | ### Summary:\nThe document describes a 4.9-mil... | The Lagoon Area at Miller/Knox Regional Shorel... | 
| Where is Hakw Hill and how long is the loop hike? | ### Summary of Lost Trail, Windy Hill Open Spa... | Hawk Hill is in the Golden Gate National Recre... | 
| What is the trail sequence for the 4.25 mile M... | ### Summary of Villa Montalvo Hike\nThe Villa ... | The 4.25 mile loop hike starts from the north ... | 

test result:
context_recall	0.102564
faithfulness	0.928641
answer_accuracy	0.000000
noise_sensitivity	0.253968

### Task 6: Improving Your Prototype
During testing, I found some guardrails are needed to make sure user's requests are realistic. I think there should be more edge cases that we need guardrails for them.

Users may not like the hiking plan, I think it needs one more step. When a plan is generated, check with user if the hiking plan ok, if not, choose a nother tour and regerenate a plan.

Users may not like go to same place multiple times. So we need somehow to remember plans generated for a user before, and try to avoid them when generate a new plan.

### Task 7: Next Steps
If I have chance, I'd like to make this hiking planner automanamous. It can automatically, say every Thuresday, post messages like "weekend is comming, any hiking ideas?' to my hiking group channel , then collect users' requests (from the group channel), then generate a hiking plan and post it in the channel.