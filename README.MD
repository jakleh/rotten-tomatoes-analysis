“Rotten Tomatoes” Web Scraper

Overview:

We want to build a database of Rotten Tomatoes reviews, where each table corresponds to a particular movie and each row is a distinct review. We will populate the database by scraping from the rotten tomatoes website. The current version of the web scraper is in this `brainstorm directory` and is called `rotten_tomatoes.py`. But, of course, it needs changes. There is also a markdown file in this brainstorm directory called `CONVERSATION_WITH_GEMINI.MD`, which contains an exchange between me and Gemini about similar architectural/implementation questions that I'll be seeking to answer in this current exchange.

***

Architecture:

Function call: `scrape_hour_sliding_window(minute_increment)`:
Every `minute_increment` minutes, we iterate through website reviews and scrape them, starting with the most recent one and until we encounter a review whose timestamp ends with “h” (since, as far as we can tell from the website, this represents the end of the sequence of reviews generated in the last hour).
We store each of the scraped reviews as a row in a relational database. For each newly-scraped review, we query the database for that review. If the review is already in the database, we move on. If not, we add the review.

Function call: `scrape_day_sliding_window(hour_increment):`
Every `hour_increment` hours, we perform the same process as in the previous function call, except this time we stop iterating/scraping once we encounter a review whose timestamp ends with “d.”
Before we store these reviews in archival storage, we first count this batch of reviews and confirm that it matches the count in the relational database for all reviews collected before the current time.
If the counts are equal, move on.
If the counts are not equal:
Step 1: Identify a review or group of consecutive reviews in the current batch of scraped reviews (i.e., the reviews that were just scraped by this `scrape_day_sliding_window` function call) that does not appear in the relational database but does appear in the reference file.
Step 2: Insert it into the database directly after whichever review appears directly before that review (or review group) in both the reference file and in the database.
(I wonder if we should spread out missing reviews as much as possible. Like, if we know some batch of two missing reviews came after a review at 3pm on March 20th and before a review at 4:30pm on the same day, then we mark the first missing review as 3:30pm on March 20th and the second as 4pm on March 20th.) Or, if it’s only one review, then it would be inserted at 3:45pm.
Then we could indicate in the table that the timestamp was estimated in the `Reconciled_Timestamp` field by setting it to `True`
We then store this current batch of scraped reviews as a .csv file in archival storage and name the file “<current timestamp>_reviews_reference”

***

Databases/Storage

Table - Relational database with the following schema:

***

Timestamp (datetime | should be eastern time)

Unique review ID (unique identifier that you create)

Subjective score (str | typically denoted as something like “3/5” or “A-”

`Reconciled_Timestamp` (boolean | indicates whether the review was missing from the hour sliding window detection (i.e., a timestamp whose exact minute is estimated--probably should figure out how we’re going to handle this before we decide on any architecture though)

`Reviewer name` (str)

`Publication name` (str)

`Top critic` (boolean)

***


Archival

We want to store the reference files that we generate on each run, as well as an occasional snapshot of the relational database, in s3
We’ll store the reference files and db snapshots in separate folders
We’ll need a method of pruning reference files and only keeping a certain number to save space/money
