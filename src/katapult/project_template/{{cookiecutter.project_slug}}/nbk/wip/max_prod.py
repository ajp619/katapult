from typing import List
from copy import copy

class Solution():
    def maxProduct(self, nums: List[int], k:int, limit: int) -> int:
        class Status():
            def __init__(self, target_sum, max_prod):
                self.sum_even = 0
                self.sum_odd = 0
                self.prod = 1
                self.zero_count = 0
                self.target_sum = target_sum
                self.max_prod = max_prod
                self.start_idx = 0

            def extend(self, idx, num):
                if num == 0:
                    self.zero_count += 1
                else:
                    self.prod = self.prod * num
                    if idx % 2 == 0:
                        self.sum_even += num
                    else:
                        self.sum_odd += num

            def drop(self, idx, num):
                self.start_idx = idx + 1
                if num == 0:
                    self.zero_count -= 1
                else:
                    self.prod = self.prod / num
                    if idx % 2 == 0:
                        self.sum_even -= num
                    else:
                        self.sum_odd -= num

            def test(self, solutions):
                prod = self.prod if self.zero_count == 0 else 0
                if self.start_idx % 2 == 0:
                    alt_sum = self.sum_even - self.sum_odd
                else:
                    alt_sum = self.sum_odd - self.sum_even

                if alt_sum == self.target_sum and prod <= self.max_prod:
                    solutions.append(prod)


        solutions = []  
        outer_status = Status(k, limit)
        outer_status.extend(0, nums[0])
        outer_status.test(solutions)
        
        for max_range in range(1, len(nums)):
            include_num = nums[max_range]

            outer_status.extend(max_range, include_num)
            outer_status.test(solutions)

            inner_status = copy(outer_status)

            for min_range in range(max_range):
                exclude_num = nums[min_range]

                inner_status.drop(min_range, exclude_num)
                inner_status.test(solutions)

        if len(solutions) == 0:
            return -1
        else:
            return max(solutions)

# --------------------------------------------------------------------------------------
solution = Solution()
solution.maxProduct([1, 2, 3], 2, 10)