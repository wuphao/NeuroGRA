class TreeNode(object):
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
class Solution:

    def partition(self, s):
        """
        :type s: str
        :rtype: List[List[str]]
        """
        n = len(s)
        ans = []
        
        f = [[True] * n for _ in range(n)]
        for i in range(n - 1, -1, -1):
            for j in range(i + 1, n):
                f[i][j] = (s[i] == s[j]) and f[i + 1][j - 1]
        
        splits = []  #存多个字串的列表
        
        def dfs(i):
            if i == n:
                ans.append(splits[:])   
                return
            for j in range(i, n):
                if f[i][j]:
                    splits.append(s[i:j + 1])
                    dfs(j + 1)  #找下一个回文串了（分割）
                    splits.pop()      
        
        dfs(0)
        return ans
    
    def kthSmallest(self, root, k):
        """
        :type root: Optional[TreeNode]
        :type k: int
        :rtype: int
        """
    

        def findk(root,k):
            if not root:
                return 
            findk(root.left,k)
            self.i+=1
            if self.i==k:
              self.res = root.val
            findk(root.right,k)

        findk(root,k)
        return self.res
    
    def bfs(self, root, depth, res):
        if not root:
            return
        if len(res) == depth:
            res.append([])
        self.bfs(root.left, depth + 1, res)
        res[depth].append(root.val)
        self.bfs(root.right, depth + 1, res)

    def levelOrder(self, root):
        res = []
        if not root:
            return res
        self.bfs(root, 0, res)
        return res

    def rightSideView(self, root):
        """
        :type root: Optional[TreeNode]
        :rtype: List[int]
        """
        res = []
        order = self.levelOrder(root)
        for o in order:
            res.append(o[len(o)-1])

        return res

    def __init__(self):
        self.prev = None

    def flatten(self, root):
        """
        :type root: Optional[TreeNode]
        :rtype: None Do not return anything, modify root in-place instead.
        """
        def dfs(node):
            if not node:
                return
            dfs(node.right)    
            dfs(node.left)       
            node.left = None    
            node.right = self.prev   
            self.prev  = node         
        dfs(root)

    def coinChange(self, coins, amount):
        """
        :type coins: List[int]
        :type amount: int
        :rtype: int
        """
        dp = [0] *(amount+1)
        for i in range(1,amount+1):
            minnum = 100000000
            for coin in coins:
                if i>=coin and dp[i-coin]!= -1:
                    minnum = min(minnum,dp[i-coin]+1)
            if minnum ==100000000:
                dp[i] = -1
            else:
                dp[i] = minnum
        return dp[amount]
    def lengthOfLIS(self, nums):
        """
        :type nums: List[int]
        :rtype: int
        """
        n = len(nums)
        dp=[1]*n
        for i in range(1,n):
            maxlength = 1
            for j in range(0,i):
                if nums[j]<nums[i]:
                    maxlength = max(maxlength,dp[j]+1)
            dp[i] = maxlength
        return max(dp)
    
    def maxProduct(self, nums):
        """
        :type nums: List[int]
        :rtype: int
        """
        dp = nums[:]
        dp1 = nums[:]
        for i in range(i,len(nums)):
            dp[i] = max(max(dp[i-1]*nums[i],dp[i]),dp1[i-1]*nums[i])
            dp1[i] = min(min(dp1[i-1]*nums[i],dp1[i]),dp[i-1]*nums[i])
        return max(dp)
    def canPartition(self, nums):
        total = sum(nums)
        if total % 2:
            return False

        target = total // 2
        dp = [False] * (target + 1)
        dp[0] = True

        for num in nums:
            for j in range(target, num - 1, -1): #让变量 j 从 target 开始，每次减 1，一直到 num（包含 num）为止  0/1背包问题
                dp[j] = dp[j] or dp[j - num]

        return dp[target]
    def wordBreak(self, s, wordDict):
        wordDictSet = set(wordDict)

        dp = [False] * (len(s) + 1)
        dp[0] = True

        for i in range(1, len(s) + 1):
            for j in range(i):
                if dp[j] and s[j:i] in wordDictSet:
                    dp[i] = True
                    break

        return dp[len(s)]
    def longestValidParentheses(self, s):
        """
        :type s: str
        :rtype: int
        """
        maxans = 0
        stack = [-1]

        for i, ch in enumerate(s):
            if ch == '(':
                stack.append(i)
            else:
                stack.pop()
                if not stack:
                    stack.append(i)
                else:
                    maxans = max(maxans, i - stack[-1])

        return maxans
        

        

        
        


        
        
        



        
def main():
    solution = Solution()
    print(solution.groupAnagrams(["eat", "tea", "tan", "ate", "nat", "bat"]))
    print(solution.maxArea([1,8,6,2,5,4,8,3,7]))
    print(solution.threeSum([-1,0,1,2,-1,-4]))
    print(solution.trap([0,1,0,2,1,0,1,3,2,1,2,1]))


if __name__ == "__main__":
    main()