"""Local concurrency stress test for reward claiming."""

import asyncio
import sys
import os
import tempfile
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.adapters.storage_sqlite.sqlite_storage import SQLiteStorage


async def run_stress_test(
    num_users: int = 20,
    num_ecards: int = 10,
    concurrent_workers: int = 10
):
    """Run concurrent claim stress test.
    
    Args:
        num_users: Number of simulated users
        num_ecards: Number of E-cards in the pool
        concurrent_workers: Number of concurrent claim operations
    """
    print(f"=== Concurrency Stress Test ===")
    print(f"Users: {num_users}")
    print(f"E-cards: {num_ecards}")
    print(f"Concurrent workers: {concurrent_workers}")
    print()
    
    # Create temporary database
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "stress_test.db"
        
        # Initialize storage
        storage = SQLiteStorage(db_path)
        await storage.initialize()
        
        # Setup E-cards
        items = [
            {
                'item_id': f'ecard_{i}',
                'type': 'JD_ECARD',
                'code': f'CODE-{i:04d}',
                'max_claims_per_item': 1
            }
            for i in range(num_ecards)
        ]
        
        await storage.sync_reward_items([{
            'pool_id': 'stress_test_pool',
            'items': items
        }])
        
        print(f"Created {num_ecards} E-cards in pool")
        
        # Create users
        users = []
        for i in range(num_users):
            user = await storage.get_or_create_user(10000 + i, f"stress_user_{i}")
            users.append(user)
        
        print(f"Created {num_users} users")
        print()
        
        # Prepare claim tasks
        async def claim_task(user, level_id):
            """Single claim operation."""
            return await storage.claim_reward('stress_test_pool', user.id, level_id)
        
        # Run concurrent claims
        print("Starting concurrent claims...")
        start_time = datetime.now()
        
        tasks = []
        for i, user in enumerate(users):
            # Each user tries to claim for a unique level (to avoid ALREADY_CLAIMED)
            tasks.append(claim_task(user, i + 1))
        
        results = await asyncio.gather(*tasks)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"Completed in {elapsed:.2f} seconds")
        print()
        
        # Analyze results
        success_count = sum(1 for r in results if r.result.value == 'SUCCESS')
        no_stock_count = sum(1 for r in results if r.result.value == 'NO_STOCK')
        already_claimed_count = sum(1 for r in results if r.result.value == 'ALREADY_CLAIMED')
        error_count = sum(1 for r in results if r.result.value == 'ERROR')
        
        print("=== Results ===")
        print(f"SUCCESS: {success_count}")
        print(f"NO_STOCK: {no_stock_count}")
        print(f"ALREADY_CLAIMED: {already_claimed_count}")
        print(f"ERROR: {error_count}")
        print()
        
        # Verify no over-issuance
        print("=== Verification ===")
        
        # Check claimed codes
        claimed_items = set()
        for user in users:
            claims = await storage.get_user_claims(user.id)
            for claim in claims:
                claimed_items.add(claim.item_id)
        
        print(f"Unique items claimed: {len(claimed_items)}")
        
        # Validate
        tests_passed = True
        
        if success_count > num_ecards:
            print(f"❌ FAIL: More successes ({success_count}) than E-cards ({num_ecards})")
            tests_passed = False
        elif success_count == num_ecards:
            print(f"✅ PASS: Exactly {num_ecards} successful claims")
        else:
            print(f"⚠️ WARN: Only {success_count} successes, expected {num_ecards}")
        
        if len(claimed_items) > num_ecards:
            print(f"❌ FAIL: Over-claimed! {len(claimed_items)} > {num_ecards}")
            tests_passed = False
        elif len(claimed_items) == min(success_count, num_ecards):
            print(f"✅ PASS: No duplicate claims")
        
        await storage.close()
        
        print()
        if tests_passed:
            print("🎉 All tests passed!")
            return 0
        else:
            print("💥 Some tests failed!")
            return 1


async def main():
    """Main entry point."""
    # Default parameters
    num_users = 50
    num_ecards = 10
    concurrent_workers = 20
    
    # Parse command line args
    if len(sys.argv) > 1:
        num_users = int(sys.argv[1])
    if len(sys.argv) > 2:
        num_ecards = int(sys.argv[2])
    if len(sys.argv) > 3:
        concurrent_workers = int(sys.argv[3])
    
    exit_code = await run_stress_test(num_users, num_ecards, concurrent_workers)
    sys.exit(exit_code)


if __name__ == '__main__':
    asyncio.run(main())
