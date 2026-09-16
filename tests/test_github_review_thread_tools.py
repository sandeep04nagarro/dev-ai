def test_github_review_thread_tools_importable():
    from agent.tools import github_reply_to_review_thread, github_resolve_review_thread

    assert callable(github_reply_to_review_thread)
    assert callable(github_resolve_review_thread)
