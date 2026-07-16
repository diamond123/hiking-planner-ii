## Project Idea

**Hiking Planner**

I live in the San Francisco Bay Area, and we often go hiking on weekends to enjoy the good weather. We have a hiking group and regularly organize hiking events. One of the organizer's tasks is to propose a hiking plan based on the preferences of most group members, remember the hiking route, check weather and trail conditions, and ultimately lead the group on a safe hike. I would like AI to take on part of the organizer's role by proposing a plan, generating a trip summary, checking weather and trail conditions, and sending out the final schedule.

Can ChatGPT do it? Partly, yes. Once I asked ChatGPT, "Can you give me a 6-mile hiking trip near north San Jose with great ocean views?" ChatGPT recommended a few tours that looked nice at first glance. It then printed out a trail sequence for one of the tours when I asked. When I checked the trail sequence on a map, I found it was wrong: it said that upon arriving at the intersection of Trail A and Trail B, I should turn left onto Trail B — but on the map, Trail A and Trail B aren't even connected!

## Solution

There are a lot of hiking reports and reviews on the web. At first, I wanted to use an LLM to scan those reports, extract hiking trail information, and use it to generate hiking routes. However, I found that this was either out of scope for an LLM or too difficult to solve. For this class project, I reduced the complexity to simply searching those reports and retrieving the one that best meets our requirements, then using an LLM to generate a summary, extract the trail sequence, and so on. Because no new route is created, the trail sequence comes 100% from the report and should therefore be accurate (since I know which data sources are reliable based on my own hiking experience).

## Audience

Day hikers living in the San Francisco Bay Area.

### Task 1: Defining Problem, Audience, and Scope

The problem and audience are as described above. I've limited this project to existing hiking tours around the San Francisco Bay Area.

### Task 2: Propose a Solution

Crawl hiking reports from websites whose content is reliable based on my experience. Use them to create a RAG application — essentially a chat app. Users can post requests like "Find a hiking route for me near Sunnyvale," "I heard there's a hiking trail leading to a cave — can you find it for me?" or "What's a good place to hike during winter?" The app searches its database, finds the record that best matches the user's request, and sends it to an LLM to generate a summary, trail sequence, parking information, and so on.

### Task 3: Dealing with the Data

Crawl hiking reports from websites whose content is reliable based on my experience. Clean, extract content from each report and split them into smaller chunks and save them to a Qdrant vector store, as we learned in class.

### Task 4: Building an End-to-End Agentic RAG Prototype

Please refer to the [LangGraph diagram](https://github.com/diamond123/hiking-planner-ii/blob/main/backend/graph.png) for a diagram of this RAG application.

It works as follows:
1. The user posts a request like "Find a hiking route for me near Sunnyvale."
2. Check whether there's enough information for planning, including hiking date, preferred location, preferred views, total distance, difficulty level, etc.
3. If not, or if the given preferences are invalid (e.g., a hiking date in the past, an unrealistic elevation gain), ask the user to provide or re-enter that information. (For now, hiking date and preferred location are required.)
4. Check weather conditions for the date and location. If the weather is bad, ask the user to choose another date or location.
5. Search the Qdrant vector store to find chunks that best match the user's request.
6. Retrieve the original hiking report using the chunk's metadata (so the chunks need to retain their origin in metadata). *This is important* because we need to make sure the hiking plan is workable and the trail sequence is correct — no mistakes are allowed.
7. Check trail conditions using Tavily Search and an LLM judge to determine whether it's OK to hike.
8. If trail conditions don't allow hiking (e.g., a trail closure), go back step 5 to search the Qdrant vector store for the next-best option (exclude the previous tours when dong the search).
9. Limit the total number of attempts (≤ 4). If no hiking tours are available, let the user know.
10. If everything checks out, send the original hiking report (not the chunks) along with the weather and trail conditions information to an LLM to generate a final plan.

### Task 5: Evals

We can use RAGAS to evaluate the RAG. However, while working on this project, I found that this isn't a standard RAG use case, because there are few connections among chunks, and all chunks are similar to some degree. For example, if a user's request is general — like "find me a place to hike 5 miles with great views" — then almost all chunks receive similarity scores that differ only slightly.

For a hiking plan, accuracy is very important. I used RAGAS to evaluate a few aspects, such as context recall, faithfulness, answer accuracy, and noise sensitivity, as shown below. I believe there's some setup issue with RAGAS's test dataset generation — the answer_accuracy score is clearly wrong, and I haven't had time to investigate further. That said, faithfulness is high, which is what I want (we don't want a hiking plan that doesn't actually work).

**Test set:**

| user_input | response | reference |
| --- | --- | --- |
| How do I get to the Lagooon Area at Miller/Kno... | ### Summary:\nThe document describes a 4.9-mil... | The Lagoon Area at Miller/Knox Regional Shorel... |
| Where is Hakw Hill and how long is the loop hike? | ### Summary of Lost Trail, Windy Hill Open Spa... | Hawk Hill is in the Golden Gate National Recre... |
| What is the trail sequence for the 4.25 mile M... | ### Summary of Villa Montalvo Hike\nThe Villa ... | The 4.25 mile loop hike starts from the north ... |

**Test results:**

| Metric | Score |
| --- | --- |
| context_recall | 0.102564 |
| faithfulness | 0.928641 |
| answer_accuracy | 0.000000 |
| noise_sensitivity | 0.253968 |

### Task 6: Improving Your Prototype

During testing, I found that some guardrails are needed to make sure users' requests are realistic. I suspect there are more edge cases that will need guardrails as well.

I also made many user-experience improvements, including optimizing the app to work smoothly in mobile browsers.

### Task 7: Next Steps

Users may not like the generated hiking plan, so I think one more step is needed: after a plan is generated, check with the user whether the plan is acceptable; if not, choose another tour and regenerate the plan.

Users may also not want to go to the same place multiple times. So we need some way to remember the plans generated for a user in the past and try to avoid repeating them when generating a new plan.

If I have the chance, I'd like to make this hiking planner autonomous. For example, it could automatically post a message like "Weekend is coming, any hiking ideas?" to my hiking group channel every Thursday, collect users' requests from the group channel, generate a hiking plan, and post it in the channel.
