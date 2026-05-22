
import requests
import os
import time
from typing import List, Dict

class FortifyGitHubIntegration:
    def __init__(self, fortify_url: str, fortify_client_id: str, fortify_client_secret: str, github_token: str, github_repo: str):
        """
        Initialize the integration.
        
        Args:
            fortify_url: Fortify on Demand URL (e.g., https://api.ams.fortify.com)
            fortify_client_id: Fortify Client ID
            fortify_client_secret: Fortify Client Secret
            github_token: GitHub personal access token
            github_repo: GitHub repository in format 'owner/repo'
        """
        self.fortify_url = fortify_url.rstrip('/')
        self.fortify_client_id = fortify_client_id
        self.fortify_client_secret = fortify_client_secret
        self.github_token = github_token
        self.github_repo = github_repo
        self.access_token = None
        self.github_headers = {
            'Authorization': f'token {github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
    
    def get_access_token(self) -> str:
        """
        Get OAuth2 access token using client credentials flow.
        
        Returns:
            Access token string
        """
        if self.access_token:
            return self.access_token
        
        url = f'{self.fortify_url}/oauth/token'
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        
        data = {
            'grant_type': 'client_credentials',
            'scope': 'api-tenant',
            'client_id': self.fortify_client_id,
            'client_secret': self.fortify_client_secret
        }
        
        try:
            response = requests.post(url, headers=headers, data=data)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get('access_token')
            return self.access_token
        except requests.exceptions.RequestException as e:
            print(f"Error getting access token: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            raise
    
    @property
    def fortify_headers(self):
        """Get headers with current access token."""
        token = self.get_access_token()
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def get_fortify_vulnerabilities(self, application_id: str, severities: List[str] = ['Critical', 'High']) -> List[Dict]:
        """
        Fetch vulnerabilities from Fortify on Demand with pagination.
        
        Args:
            application_id: Fortify release ID
            severities: List of severity levels to fetch
        
        Returns:
            List of vulnerability dictionaries
        """
        url = f'{self.fortify_url}/api/v3/releases/{application_id}/vulnerabilities'
        all_vulnerabilities = []
        offset = 0
        limit = 50  # Maximum allowed by FoD API
        
        while True:
            params = {
                'limit': limit,
                'offset': offset,
                'filters': f'severityString:{"+".join(severities)}'
            }
            
            try:
                print(f"Requesting page (offset={offset})...")
                
                response = requests.get(url, headers=self.fortify_headers, params=params)
                response.raise_for_status()
                data = response.json()
                items = data.get('items', [])
                
                if not items:
                    break
                
                all_vulnerabilities.extend(items)
                print(f"Fetched {len(items)} vulnerabilities (total: {len(all_vulnerabilities)})")
                
                # Check if there are more results
                total_count = data.get('totalCount', len(all_vulnerabilities))
                if len(all_vulnerabilities) >= total_count:
                    break
                
                offset += limit
                
            except requests.exceptions.JSONDecodeError as e:
                print(f"JSON Decode Error: {e}")
                print(f"Full Response Text: {response.text}")
                break
            except requests.exceptions.RequestException as e:
                print(f"Error fetching Fortify vulnerabilities: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response Status: {e.response.status_code}")
                    print(f"Response Text: {e.response.text}")
                break
        
        return all_vulnerabilities
    
    def get_vulnerability_details(self, application_id: str, vuln_id: str) -> Dict:
        """
        Get detailed information about a specific vulnerability.
        
        Args:
            application_id: Fortify release ID
            vuln_id: Specific vulnerability ID
        
        Returns:
            Detailed vulnerability information
        """
        details = {}
        
        # Try the summary endpoint which has more details
        summary_url = f'{self.fortify_url}/api/v3/releases/{application_id}/vulnerabilities/{vuln_id}/summary'
        try:
            response = requests.get(summary_url, headers=self.fortify_headers)
            if response.status_code == 200:
                summary_data = response.json()
                details.update(summary_data)
        except Exception as e:
            pass
        
        # Get traces which contain code snippets
        traces_url = f'{self.fortify_url}/api/v3/releases/{application_id}/vulnerabilities/{vuln_id}/traces'
        try:
            response = requests.get(traces_url, headers=self.fortify_headers)
            if response.status_code == 200:
                traces_data = response.json()
                details['traces'] = traces_data.get('traces', [])
        except:
            pass
        
        # Get audit comments/recommendations
        comments_url = f'{self.fortify_url}/api/v3/releases/{application_id}/vulnerabilities/{vuln_id}/comments'
        try:
            response = requests.get(comments_url, headers=self.fortify_headers)
            if response.status_code == 200:
                audits_data = response.json()
                details['audits'] = audits_data.get('items', [])
        except Exception as e:
            pass
        
        return details
    
    def get_source_code_snippet(self, file_path: str, line_number: int, context_lines: int = 5) -> str:
        """
        Fetch source code from GitHub repository.
        
        Args:
            file_path: Path to file in repository
            line_number: Line number of the vulnerability
            context_lines: Number of lines before/after to include
        
        Returns:
            Code snippet string
        """
        try:
            # Get file content from GitHub
            url = f'https://api.github.com/repos/{self.github_repo}/contents/{file_path}'
            response = requests.get(url, headers=self.github_headers)
            
            if response.status_code == 200:
                content_data = response.json()
                import base64
                content = base64.b64decode(content_data['content']).decode('utf-8')
                lines = content.split('\n')
                
                # Extract lines around the vulnerability
                start = max(0, line_number - context_lines - 1)
                end = min(len(lines), line_number + context_lines)
                
                snippet_lines = []
                for i in range(start, end):
                    line_num = i + 1
                    line_content = lines[i].rstrip()
                    
                    # Add simple arrow to highlight the vulnerable line
                    if line_num == line_number:
                        snippet_lines.append(f"→   {line_num:3d} | {line_content}")
                    else:
                        snippet_lines.append(f"    {line_num:3d} | {line_content}")
                
                return '\n'.join(snippet_lines)
        except Exception as e:
            return None
        
        return None
    
    def check_github_issue_exists(self, fortify_issue_id: str) -> tuple[bool, int, str, bool]:
        """
        Check if a GitHub issue already exists for this Fortify issue.
        
        Args:
            fortify_issue_id: Fortify issue ID
        
        Returns:
            Tuple of (exists, issue_number, state, has_pr)
            - exists: Whether issue exists
            - issue_number: GitHub issue number
            - state: 'open' or 'closed'
            - has_pr: Whether issue has associated PRs
        """
        url = f'https://api.github.com/repos/{self.github_repo}/issues'
        params = {
            'state': 'all',
            'labels': f'fortify-id-{fortify_issue_id}'
        }
        
        try:
            response = requests.get(url, headers=self.github_headers, params=params)
            response.raise_for_status()
            issues = response.json()
            if len(issues) > 0:
                issue = issues[0]
                issue_number = issue['number']
                state = issue['state']
                
                # Check if issue has associated PRs by label
                has_pr = self.check_issue_has_pr(fortify_issue_id)
                
                return True, issue_number, state, has_pr
            return False, None, None, False
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"GitHub repository not found or token lacks access: {self.github_repo}")
                print(f"Verify: 1) Repository exists, 2) Token has 'repo' scope, 3) You have access")
            return False, None, None, False
        except requests.exceptions.RequestException as e:
            return False, None, None, False
    
    def check_issue_has_pr(self, fortify_id: str) -> bool:
        """
        Check if there are any PRs with the matching Fortify ID label.
        
        Args:
            fortify_id: The Fortify vulnerability ID
        
        Returns:
            True if PRs exist with matching label
        """
        prs = self.get_prs_for_issue(fortify_id)
        return len(prs) > 0
    
    def get_prs_for_issue(self, fortify_id: str) -> List[Dict]:
        """
        Get all PRs with the matching Fortify ID label.
        
        Args:
            fortify_id: The Fortify vulnerability ID
        
        Returns:
            List of PR data dictionaries that have the matching label
        """
        url = f'https://api.github.com/repos/{self.github_repo}/pulls'
        params = {
            'state': 'all',
            'per_page': 100
        }
        
        label_to_find = f'fortify-id-{fortify_id}'
        matching_prs = []
        
        try:
            response = requests.get(url, headers=self.github_headers, params=params)
            response.raise_for_status()
            pulls = response.json()
            
            for pr in pulls:
                # Check if PR has the Fortify ID label
                pr_labels = [label.get('name', '') for label in pr.get('labels', [])]
                if label_to_find in pr_labels:
                    matching_prs.append({
                        'number': pr.get('number'),
                        'state': pr.get('state'),
                        'title': pr.get('title'),
                        'user': pr.get('user', {}).get('login', 'unknown'),
                        'html_url': pr.get('html_url'),
                        'merged': pr.get('merged', False)
                    })
            
            return matching_prs
        except requests.exceptions.RequestException:
            return []
    
    def close_github_issue(self, issue_number: int, reason: str = None) -> bool:
        """
        Close a GitHub issue.
        
        Args:
            issue_number: GitHub issue number
            reason: Optional comment explaining why issue is being closed
        
        Returns:
            True if successfully closed
        """
        url = f'https://api.github.com/repos/{self.github_repo}/issues/{issue_number}'
        payload = {'state': 'closed'}
        
        try:
            # Add comment if reason provided
            if reason:
                comment_url = f'https://api.github.com/repos/{self.github_repo}/issues/{issue_number}/comments'
                comment_payload = {'body': reason}
                requests.post(comment_url, headers=self.github_headers, json=comment_payload)
                time.sleep(0.5)  # Rate limiting
            
            response = requests.patch(url, headers=self.github_headers, json=payload)
            response.raise_for_status()
            print(f"Closed issue #{issue_number}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error closing issue #{issue_number}: {e}")
            return False
    
    def check_vulnerability_status(self, application_id: str, vuln_id: str) -> str:
        """
        Check the status of a vulnerability in Fortify.
        
        Args:
            application_id: Fortify application ID
            vuln_id: Vulnerability ID
        
        Returns:
            Status string: 'active', 'fixed', 'wont_fix', 'unknown'
        """
        url = f'{self.fortify_url}/api/v3/releases/{application_id}/vulnerabilities/{vuln_id}'
        
        try:
            response = requests.get(url, headers=self.fortify_headers)
            response.raise_for_status()
            vuln = response.json()
            
            # Check various status indicators
            status = vuln.get('status', '').lower()
            closed = vuln.get('closed', False)
            removed = vuln.get('removed', False)
            
            # Check analysis state
            analysis = vuln.get('analysisState', '').lower()
            
            if removed or 'removed' in status:
                return 'fixed'
            if closed or 'closed' in status:
                return 'fixed'
            if 'remediated' in status or 'fixed' in status:
                return 'fixed'
            if 'not an issue' in status or 'false positive' in analysis:
                return 'wont_fix'
            if 'will not fix' in analysis or 'wont fix' in analysis:
                return 'wont_fix'
            
            return 'active'
        except requests.exceptions.RequestException:
            return 'unknown'
    
    def update_github_issue(self, issue_number: int, vulnerability: Dict, details: Dict = None) -> Dict:
        """
        Update an existing GitHub issue.
        
        Args:
            issue_number: GitHub issue number
            vulnerability: Fortify vulnerability data
            details: Additional vulnerability details
        
        Returns:
            Updated GitHub issue data
        """
        severity = vulnerability.get('severityString', vulnerability.get('severity', 'Unknown'))
        category = vulnerability.get('category', vulnerability.get('categoryName', 'Unknown'))
        issue_name = vulnerability.get('issueName', vulnerability.get('category', 'Security Issue'))
        
        title = f"[Fortify {severity}] {issue_name}"
        body = self._create_issue_body(vulnerability, details)
        
        url = f'https://api.github.com/repos/{self.github_repo}/issues/{issue_number}'
        payload = {
            'title': title,
            'body': body
        }
        
        try:
            response = requests.patch(url, headers=self.github_headers, json=payload)
            response.raise_for_status()
            issue = response.json()
            print(f"Updated GitHub issue #{issue['number']}: {title}")
            
            # Assign to Copilot
            self.assign_to_copilot(issue_number, vulnerability, details)
            
            return issue
        except requests.exceptions.RequestException as e:
            print(f"Error updating GitHub issue: {e}")
            return None
    
    def assign_to_copilot(self, issue_number: int, vulnerability: Dict = None, details: Dict = None) -> bool:
        """
        Assign issue to GitHub Copilot using GraphQL API with custom instructions.
        
        Args:
            issue_number: GitHub issue number
            vulnerability: Vulnerability data for context
            details: Additional vulnerability details
        
        Returns:
            True if successful
        """
        try:
            owner, repo = self.github_repo.split('/')
            token = self.github_headers["Authorization"].split()[-1]
            
            query = """
            query($owner: String!, $name: String!, $issueNumber: Int!) {
              repository(owner: $owner, name: $name) {
                id
                issue(number: $issueNumber) { id }
                suggestedActors(capabilities: [CAN_BE_ASSIGNED], first: 100) {
                  nodes { login ... on Bot { id } }
                }
              }
            }
            """
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'GraphQL-Features': 'issues_copilot_assignment_api_support'
            }
            
            response = requests.post(
                'https://api.github.com/graphql',
                headers=headers,
                json={'query': query, 'variables': {'owner': owner, 'name': repo, 'issueNumber': issue_number}}
            )
            
            if response.status_code != 200:
                print(f"⚠️  GraphQL query failed: {response.status_code}")
                return False
            
            data = response.json()
            if 'errors' in data:
                print(f"⚠️  GraphQL errors: {data['errors']}")
                return False
            
            repo_id = data['data']['repository']['id']
            issue_id = data['data']['repository']['issue']['id']
            copilot = next((a for a in data['data']['repository']['suggestedActors']['nodes'] 
                           if a['login'] == 'copilot-swe-agent'), None)
            
            if not copilot:
                print(f"⚠️  Copilot bot not available for this repository")
                return False
            
            # Build custom instructions from vulnerability data
            custom_instructions = ""
            if vulnerability:
                file_location = vulnerability.get('primaryLocation', 'unknown')
                line_num = vulnerability.get('lineNumber', 'N/A')
                severity = vulnerability.get('severityString', 'unknown')
                category = vulnerability.get('category', 'Security Issue')
                issue_name = vulnerability.get('issueName', category)
                
                # Get recommendations from Fortify
                rec_summary = ""
                if details and details.get('audits'):
                    aviator_comments = [a for a in details['audits'] if 'Aviator' in a.get('username', '')]
                    if aviator_comments:
                        recs = [a.get('comment', '')[:150] for a in aviator_comments[:2]]
                        rec_summary = " Key recommendations: " + "; ".join(recs)
                
                # Get the Fortify ID label for this issue
                fortify_label = f"fortify-id-{vulnerability.get('id')}"
                
                custom_instructions = f"""Review the issue description for full vulnerability details including code snippets and Fortify analysis.

Fix the {severity} severity {category} vulnerability in {file_location} at line {line_num}.{rec_summary}

Create a PR that:
- Fixes the security vulnerability using secure coding practices
- References this issue with "Fixes #{issue_number}"
- Uses title: [Security Fix] {issue_name}
- Includes description explaining the vulnerability and fix
- Maintains backward compatibility
- Adds tests if applicable
- IMPORTANT: Add label "{fortify_label}" to the PR (use GitHub API or gh CLI)

PR Status:
- Start as draft while working
- Mark as "Ready for review" (not draft) when all changes are complete and tests pass
- Do NOT add any reviewers manually
- The PR should be ready for immediate human review when you're done

Note: The label {fortify_label} is required to track this PR's relationship to the security issue."""
            
            mutation = """
            mutation($issueId: ID!, $actorIds: [ID!]!, $repoId: ID!, $instructions: String!) {
              replaceActorsForAssignable(input: {
                assignableId: $issueId,
                actorIds: $actorIds,
                agentAssignment: {
                  targetRepositoryId: $repoId,
                  baseRef: "main",
                  customInstructions: $instructions
                }
              }) {
                assignable {
                  ... on Issue {
                    assignees(first: 10) {
                      nodes { login }
                    }
                  }
                }
              }
            }
            """
            
            response = requests.post(
                'https://api.github.com/graphql',
                headers=headers,
                json={
                    'query': mutation,
                    'variables': {
                        'issueId': issue_id,
                        'actorIds': [copilot['id']],
                        'repoId': repo_id,
                        'instructions': custom_instructions
                    }
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'errors' not in result:
                    assignees = result['data']['replaceActorsForAssignable']['assignable']['assignees']['nodes']
                    print(f"✅ Assigned issue #{issue_number} to Copilot")
                    return True
                else:
                    print(f"⚠️  Assignment failed: {result['errors']}")
                    return False
            else:
                print(f"⚠️  Assignment request failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"⚠️  Error assigning to Copilot: {e}")
            return False
    
    def create_github_issue(self, vulnerability: Dict, details: Dict = None, update_existing: bool = True, force_update: bool = False) -> Dict:
        """
        Create a GitHub issue from a Fortify vulnerability.
        
        Args:
            vulnerability: Fortify vulnerability data
            details: Additional vulnerability details
            update_existing: If True, update existing issues (unless closed or has PR)
            force_update: If True, bypass closed/PR checks (for local testing)
        
        Returns:
            Created or updated GitHub issue data
        """
        fortify_id = vulnerability.get('id')
        
        # Check if issue already exists
        exists, issue_number, state, has_pr = self.check_github_issue_exists(fortify_id)
        if exists:
            if not force_update:
                # Skip closed issues - respect manual closure
                if state == 'closed':
                    print(f"Issue #{issue_number} is closed, skipping...")
                    return None
                
                # Skip issues with PRs - Copilot is already working on it
                if has_pr:
                    print(f"Issue #{issue_number} has associated PR(s), skipping update...")
                    return None
            else:
                # Force update mode - show what we're bypassing
                status_notes = []
                if state == 'closed':
                    status_notes.append("closed")
                if has_pr:
                    status_notes.append("has PR")
                if status_notes:
                    print(f"🔧 Force update mode: Issue #{issue_number} ({', '.join(status_notes)}), updating anyway...")
            
            if update_existing:
                print(f"Issue #{issue_number} exists for Fortify ID {fortify_id}, updating...")
                return self.update_github_issue(issue_number, vulnerability, details)
            else:
                print(f"Issue already exists for Fortify ID: {fortify_id}, skipping")
                return None
        
        # Prepare issue data - FoD uses different field names
        severity = vulnerability.get('severityString', vulnerability.get('severity', 'Unknown'))
        category = vulnerability.get('category', vulnerability.get('categoryName', 'Unknown'))
        issue_name = vulnerability.get('issueName', vulnerability.get('category', 'Security Issue'))
        
        title = f"[Fortify {severity}] {issue_name}"
        
        body = self._create_issue_body(vulnerability, details)
        
        # Create labels
        labels = [
            'security',
            'fortify',
            f"fortify-id-{fortify_id}",
            f"severity-{severity.lower()}",
            category.lower().replace(' ', '-')
        ]
        
        # Create the issue
        url = f'https://api.github.com/repos/{self.github_repo}/issues'
        payload = {
            'title': title,
            'body': body,
            'labels': labels
        }
        
        try:
            response = requests.post(url, headers=self.github_headers, json=payload)
            response.raise_for_status()
            issue = response.json()
            print(f"Created GitHub issue #{issue['number']}: {title}")
            
            # Assign to Copilot with instructions
            issue_number = issue['number']
            try:
                self.assign_to_copilot(issue_number, vulnerability, details)
            except:
                pass
            
            return issue
        except requests.exceptions.RequestException as e:
            print(f"Error creating GitHub issue: {e}")
            return None
    
    def _create_issue_body(self, vulnerability: Dict, details: Dict = None) -> str:
        """
        Create formatted issue body from vulnerability data.
        
        Args:
            vulnerability: Fortify vulnerability data
            details: Additional vulnerability details
        
        Returns:
            Formatted markdown string
        """
        severity = vulnerability.get('severityString', vulnerability.get('severity', 'Unknown'))
        category = vulnerability.get('category', vulnerability.get('categoryName', 'Unknown'))
        vuln_id = vulnerability.get('id', vulnerability.get('vulnId', 'Unknown'))
        release_id = details.get('releaseId') if details else None
        
        body_parts = [
            "## Security Vulnerability Detected by Fortify on Demand",
            "",
            f"**Severity:** {severity}",
            f"**Category:** {category}",
            f"**Kingdom:** {vulnerability.get('kingdom', 'Unknown')}",
            f"**Fortify Vulnerability ID:** {vuln_id}",
            ""
        ]
        
        # Add link to Fortify dashboard
        if release_id and vuln_id:
            fortify_url = f"https://ams.fortify.com/Releases/{release_id}/Issues/{vuln_id}"
            body_parts.extend([
                f"🔗 **[View in Fortify Dashboard]({fortify_url})** ↗️",
                ""
            ])
        elif vuln_id:
            fortify_url = f"https://ams.fortify.com"
            body_parts.extend([
                f"🔗 **[Open Fortify Dashboard]({fortify_url})** ↗️ (Search for ID: {vuln_id})",
                ""
            ])
        
        # Add file location if available
        primary_location = vulnerability.get('primaryLocationFull') or vulnerability.get('fullFileName')
        line_number = vulnerability.get('lineNumber')
        
        # Add detailed description from vulnerability or details
        summary = vulnerability.get('summary') or (details.get('summary') if details else None)
        explanation = vulnerability.get('explanation') or (details.get('explanation') if details else None)
        
        if summary:
            body_parts.extend([
                "### Summary",
                summary,
                ""
            ])
        
        if explanation:
            body_parts.extend([
                "### Explanation",
                explanation,
                ""
            ])
        
        # Try to extract code snippet from traces
        code_added = False
        
        # First try to get code from GitHub repository
        if primary_location and line_number:
            code_snippet = self.get_source_code_snippet(primary_location, line_number)
            if code_snippet:
                lang = 'python' if primary_location.endswith('.py') else ''
                lang = 'yaml' if primary_location.endswith(('.yaml', '.yml')) else lang
                lang = 'javascript' if primary_location.endswith(('.js', '.ts')) else lang
                
                body_parts.extend([
                    "---",
                    "",
                    "## 🔍 Vulnerable Code Location",
                    "",
                    "📄 **File:** `{}`".format(primary_location),
                    "🎯 **Line:** {}".format(line_number),
                    "",
                    "### Code Snippet",
                    "",
                    f"```{lang}",
                    code_snippet,
                    "```",
                    ""
                ])
                code_added = True
        
        # Fall back to traces if GitHub fetch didn't work
        if not code_added and details and details.get('traces'):
            traces = details['traces']
            if isinstance(traces, list) and len(traces) > 0:
                # Get the primary trace (usually the first one)
                trace = traces[0]
                trace_entries = trace.get('traceEntries', [])
                
                if trace_entries:
                    body_parts.extend([
                        "### 📍 Code Snippet",
                        ""
                    ])
                    
                    # Find the entry matching the primary location
                    for entry in trace_entries:
                        entry_file = entry.get('location', {}).get('path', '')
                        entry_line = entry.get('location', {}).get('line', 0)
                        snippet = entry.get('snippet', '')
                        
                        # If this matches our primary location or has a snippet, show it
                        if snippet and (not line_number or entry_line == line_number or not primary_location or primary_location in entry_file):
                            lang = 'python' if primary_location and primary_location.endswith('.py') else ''
                            lang = 'yaml' if primary_location and primary_location.endswith(('.yaml', '.yml')) else lang
                            lang = 'javascript' if primary_location and primary_location.endswith(('.js', '.ts')) else lang
                            
                            body_parts.extend([
                                f"```{lang}",
                                snippet.strip(),
                                "```",
                                ""
                            ])
                            code_added = True
                            break
        
        # Add data flow trace if available (don't duplicate with code snippet section)
        if details and details.get('traces') and len(details['traces']) > 0:
            traces = details['traces']
            body_parts.extend([
                "### 🔄 Data Flow Trace",
                ""
            ])
            for i, trace in enumerate(traces[:2]):  # Show first 2 traces
                trace_entries = trace.get('traceEntries', [])
                if trace_entries:
                    body_parts.append(f"**Trace {i+1}:**")
                    for entry in trace_entries[:5]:  # Show first 5 entries
                        file_path = entry.get('location', {}).get('path', 'Unknown')
                        line_num = entry.get('location', {}).get('line', 'N/A')
                        body_parts.append(f"- `{file_path}:{line_num}`")
                    body_parts.append("")
        
        # Add recommendations
        recommendations = vulnerability.get('recommendations') or (details.get('recommendations') if details else None)
        if recommendations:
            body_parts.extend([
                "### Recommendations",
                recommendations,
                ""
            ])
        
        # Add Fortify audit comments (like Aviator recommendations)
        if details and details.get('audits'):
            audits = details['audits']
            aviator_comments = [a for a in audits if 'Aviator' in a.get('username', '') or 'SAST' in a.get('username', '')]
            
            if aviator_comments:
                body_parts.extend([
                    "---",
                    "",
                    "### 🤖 Fortify Analysis & Recommendations",
                    ""
                ])
                for audit in aviator_comments[:3]:  # Show up to 3 audit comments
                    comment = audit.get('comment', '').strip()
                    user = audit.get('username', 'Fortify')
                    if comment:
                        body_parts.extend([
                            f"#### 🔍 {user}",
                            "",
                            "> [!IMPORTANT]",
                            f"> {comment.replace(chr(10), chr(10) + '> ')}",
                            ""
                        ])
        
        # Add compliance information
        compliance = vulnerability.get('complianceCategories', [])
        if compliance:
            body_parts.extend([
                "### Compliance Violations",
                ""
            ])
            for cat in compliance[:3]:  # Show first 3 categories
                cat_name = cat.get('categoryName', 'Unknown')
                items = cat.get('complianceItems', [])
                if items:
                    body_parts.append(f"**{cat_name}:**")
                    for item in items[:3]:  # Show first 3 items per category
                        body_parts.append(f"- {item.get('complianceRule', 'Unknown')}")
                    body_parts.append("")
        
        # Add detailed description if available
        if details:
            if details.get('brief'):
                body_parts.extend([
                    "### Description",
                    details.get('brief'),
                    ""
                ])
            
            if details.get('detail'):
                body_parts.extend([
                    "### Details",
                    details.get('detail'),
                    ""
                ])
            
            if details.get('recommendation'):
                body_parts.extend([
                    "### Recommendation",
                    details.get('recommendation'),
                    ""
                ])
        
        return "\n".join(body_parts)
    
    def cleanup_resolved_issues(self, application_id: str):
        """
        Check existing GitHub issues and close those with:
        - Closed PRs (not merged)
        - Resolved vulnerabilities in Fortify (Fixed/Won't Fix)
        
        Args:
            application_id: Fortify application ID
        """
        # Get all open Fortify issues
        url = f'https://api.github.com/repos/{self.github_repo}/issues'
        params = {
            'state': 'open',
            'labels': 'fortify',
            'per_page': 100
        }
        
        try:
            response = requests.get(url, headers=self.github_headers, params=params)
            response.raise_for_status()
            issues = response.json()
            
            for issue in issues:
                issue_number = issue['number']
                
                # Extract Fortify ID from labels
                fortify_id = None
                for label in issue.get('labels', []):
                    label_name = label.get('name', '')
                    if label_name.startswith('fortify-id-'):
                        fortify_id = label_name.replace('fortify-id-', '')
                        break
                
                if not fortify_id:
                    continue
                
                time.sleep(0.3)  # Rate limiting
                
                # Check if vulnerability is resolved in Fortify
                vuln_status = self.check_vulnerability_status(application_id, fortify_id)
                
                if vuln_status == 'fixed':
                    reason = "🎉 **Auto-closing:** Vulnerability marked as Fixed in Fortify."
                    self.close_github_issue(issue_number, reason)
                    print(f"Closed issue #{issue_number} - vulnerability fixed in Fortify")
                    continue
                
                if vuln_status == 'wont_fix':
                    reason = "⚠️ **Auto-closing:** Vulnerability marked as Won't Fix in Fortify."
                    self.close_github_issue(issue_number, reason)
                    print(f"Closed issue #{issue_number} - marked won't fix in Fortify")
                    continue
                
                # Check for PRs with matching Fortify ID label (deterministic way)
                prs = self.get_prs_for_issue(fortify_id)
                if prs:
                    print(f"  Found {len(prs)} PR(s) with label fortify-id-{fortify_id}")
                    # Only close if PR was closed WITHOUT merging (failed attempt)
                    # Note: GitHub auto-closes issues when PR with "Fixes #issue" is merged
                    for pr in prs:
                        pr_state = pr.get('state')
                        pr_number = pr.get('number')
                        pr_user = pr.get('user', 'unknown')
                        is_merged = pr.get('merged', False)
                        
                        print(f"    PR #{pr_number} by {pr_user}: {pr_state} (merged: {is_merged})")
                        
                        # Only close issue if PR was closed WITHOUT merging
                        if pr_state == 'closed' and not is_merged:
                            reason = f"❌ **Auto-closing:** PR #{pr_number} was closed without merging. The fix attempt was abandoned."
                            self.close_github_issue(issue_number, reason)
                            print(f"Closed issue #{issue_number} - PR #{pr_number} closed without merge")
                            break
                        elif pr_state == 'closed' and is_merged:
                            print(f"    PR #{pr_number} merged - GitHub auto-closed issue (skipping)")
                            break
                        
                        time.sleep(0.3)  # Rate limiting
        
        except requests.exceptions.RequestException as e:
            print(f"Error during cleanup: {e}")
    
    def sync_vulnerabilities(self, application_id: str, fetch_details: bool = True, update_existing: bool = True, force_update: bool = False):
        """
        Main method to sync Fortify vulnerabilities to GitHub issues.
        
        Args:
            application_id: Fortify application ID
            fetch_details: Whether to fetch detailed vulnerability info
            update_existing: If True, update existing issues instead of skipping
            force_update: If True, bypass closed/PR checks (useful for local testing)
        """
        print(f"Fetching vulnerabilities from Fortify application {application_id}...")
        vulnerabilities = self.get_fortify_vulnerabilities(application_id)
        
        print(f"Found {len(vulnerabilities)} critical/high severity issues")
        
        if force_update:
            print("🔧 Force update mode enabled - will update all issues (closed, with PRs, etc.)")
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        # First, check existing issues for status changes (skip in force mode)
        if not force_update:
            print("\nChecking existing issues for closed PRs or resolved vulnerabilities...")
            self.cleanup_resolved_issues(application_id)
        else:
            print("\n⏭️  Skipping cleanup in force update mode...")
        
        print("\nProcessing vulnerabilities...")
        for i, vuln in enumerate(vulnerabilities):
            # Rate limiting - small delay between vulnerabilities
            if i > 0:
                time.sleep(0.5)
            
            details = None
            if fetch_details:
                details = self.get_vulnerability_details(application_id, vuln['id'])
                time.sleep(0.3)  # Rate limiting after details fetch
            
            result = self.create_github_issue(vuln, details, update_existing=update_existing, force_update=force_update)
            
            if result:
                if 'updated_at' in result and result.get('created_at') != result.get('updated_at'):
                    updated_count += 1
                else:
                    created_count += 1
            else:
                skipped_count += 1            
        
        print(f"\nSync completed:")
        print(f"  - Created: {created_count}")
        print(f"  - Updated: {updated_count}")
        print(f"  - Skipped: {skipped_count}")

def main():
    # Configuration from environment variables
    fortify_url = os.getenv('FORTIFY_URL', 'https://api.ams.fortify.com')
    fortify_client_id = os.getenv('FORTIFY_CLIENT_ID')
    fortify_client_secret = os.getenv('FORTIFY_CLIENT_SECRET')
    github_token = os.getenv('GITHUB_TOKEN')
    github_repo = os.getenv('GITHUB_REPO')
    application_id = os.getenv('FORTIFY_APP_ID', '1663485')
    force_update = os.getenv('FORCE_UPDATE', 'false').lower() == 'true'
    
    if not all([fortify_client_id, fortify_client_secret, github_token, github_repo]):
        print("Error: Missing required environment variables")
        print("Required: FORTIFY_CLIENT_ID, FORTIFY_CLIENT_SECRET, GITHUB_TOKEN, GITHUB_REPO")
        return
    
    # Clean up github_repo if it contains a full URL
    if 'github.com/' in github_repo:
        github_repo = github_repo.split('github.com/')[-1].rstrip('/')
        print(f"Cleaned GitHub repo to: {github_repo}")
    
    # Test GitHub access first
    print(f"\nTesting GitHub access to: {github_repo}")
    
    # Test GitHub access
    test_headers = {'Authorization': f'token {github_token}', 'Accept': 'application/vnd.github.v3+json'}
    test_url = f'https://api.github.com/repos/{github_repo}'
    try:
        test_response = requests.get(test_url, headers=test_headers)
        if test_response.status_code != 200:
            print(f"Error: Cannot access GitHub repo '{github_repo}'")
            print(f"Response: {test_response.status_code} - {test_response.json().get('message', 'Unknown error')}")
            return
    except Exception as e:
        print(f"Error testing GitHub access: {e}")
        return
    
    # Initialize integration
    integration = FortifyGitHubIntegration(
        fortify_url=fortify_url,
        fortify_client_id=fortify_client_id,
        fortify_client_secret=fortify_client_secret,
        github_token=github_token,
        github_repo=github_repo
    )
    
    # Sync vulnerabilities
    integration.sync_vulnerabilities(application_id, fetch_details=True, force_update=force_update)

if __name__ == '__main__':
    main()