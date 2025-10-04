#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Build a Telegram bot that searches militaria321.com for new listings and sends push notifications.
  Critical issue: Bot was returning 0 results for "Brieföffner" despite 40+ actual results on site.
  Fix: Correct search parameter from 'wort' to 'q', improved HTML parsing to extract titles/prices correctly.

backend:
  - task: "Fix militaria321 provider search parameter"
    implemented: true
    working: true
    file: "backend/providers/militaria321.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: true
        agent: "main"
        comment: "Fixed search parameter from 'wort' to 'q', updated parsing to extract titles from auction links correctly. Tested with Brieföffner (25 results), Kappmesser (8 results), Helm (73 results), uhr (19 results with no timestamp false positives)."
      - working: true
        agent: "testing"
        comment: "✓ VERIFIED: Militaria321 search working perfectly. Brieföffner: 25 results, Kappmesser: 8 results, uhr: 19 results with NO timestamp false positives. Non-existent terms correctly return 0 results. Search parameter 'q' working correctly."

  - task: "Price parsing and German formatting"
    implemented: true
    working: true
    file: "backend/providers/militaria321.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: true
        agent: "main"
        comment: "parse_price() and format_price_de() methods working correctly. Tested: '249,00 €', '20,00 €', '5,00 €' all formatted properly."

  - task: "Title-only keyword matching (no timestamp false positives)"
    implemented: true
    working: true
    file: "backend/providers/militaria321.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: true
        agent: "main"
        comment: "matches_keyword() correctly filters timestamps. Keyword 'uhr' returns 19 items with 'Uhr' in title (e.g. 'Turm mit Uhr') but not timestamps like '07:39 Uhr'."

  - task: "/loeschen command with confirmation"
    implemented: true
    working: "NA"
    file: "backend/bot/handlers.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Re-enabled with inline confirmation buttons. Needs end-to-end testing with bot."

  - task: "First-run sample with count reporting"
    implemented: true
    working: "NA"
    file: "backend/bot/handlers.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Implemented in perform_setup_search_with_count(). Needs end-to-end testing with bot."

frontend:
  - task: "N/A - Backend bot only"
    implemented: false
    working: "NA"
    file: "N/A"
    stuck_count: 0
    priority: "low"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "This project is backend Telegram bot only, no frontend."

metadata:
  created_by: "main_agent"
  version: "1.0"
  test_sequence: 1
  run_ui: false

test_plan:
  current_focus:
    - "Provider search and parsing fixes"
    - "End-to-end bot testing with Telegram"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
  - agent: "main"
    message: "Fixed militaria321 search parameter (changed from 'wort' to 'q') and improved HTML parsing to extract titles from auction links. Provider now correctly retrieves 25 results for 'Brieföffner' (was 0 before). German price formatting working. Bot restarted and sending notifications. Ready for comprehensive Telegram bot command testing."
  - agent: "main"
    message: "Calling backend testing agent to test all Telegram bot commands: /start, /hilfe, /suche (with various keywords including diacritics), /liste, /testen, /loeschen (with confirmation), /pausieren, /fortsetzen. Need to verify: 1) Keyword search returns correct count, 2) Price formatting in German (e.g., '249,00 €'), 3) No timestamp false positives for 'uhr' keyword, 4) Delete confirmation flow works."