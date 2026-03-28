    async def test_get_live_prs_fallback_no_token(self):
        from src.api.state import StateManager
        sm = StateManager()
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = False
            result = await sm.get_live_prs()
            assert len(result) == 0
